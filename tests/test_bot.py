from datetime import date, datetime, timedelta

import pytest

from app.bot import REFLECT_QUESTIONS, Bot
from app.db import Database
from app.notion import NotionError

TODAY = date(2026, 11, 1)
REFLECT_Q2 = REFLECT_QUESTIONS[1]


class FakeNotion:
    """In-memory stand-in for the Notion API, storing pages in their API shape."""

    def __init__(self):
        self.pages: dict[str, dict] = {}
        self.blocks = 0
        self.fail = False

    def _check(self):
        if self.fail:
            raise NotionError("503: down")

    def create_page(self, ds, properties, children=None):
        self._check()
        pid = f"p{len(self.pages) + 1}"
        self.pages[pid] = {"id": pid, "ds": ds, "properties": _readable(properties),
                           "children": list(children or []), "trash": False}
        return pid

    def query(self, ds, filter=None, sorts=None, limit=200):
        self._check()
        rows = [p for p in self.pages.values() if p["ds"] == ds and not p["trash"]]
        return [p for p in rows if _matches(p, filter)]

    def get_page(self, pid):
        return self.pages[pid]

    def update_page(self, pid, properties=None, in_trash=None):
        self._check()
        if properties:
            self.pages[pid]["properties"].update(_readable(properties))
        if in_trash is not None:
            self.pages[pid]["trash"] = in_trash

    def append_blocks(self, pid, children):
        self._check()
        ids = []
        for block in children:
            self.blocks += 1
            block = {**block, "id": f"b{self.blocks}"}
            self.pages[pid]["children"].append(block)
            ids.append(block["id"])
        return ids

    def delete_block(self, bid):
        for p in self.pages.values():
            p["children"] = [b for b in p["children"] if b.get("id") != bid]

    def trash(self, pid):
        self.update_page(pid, in_trash=True)

    # helpers for assertions
    def live(self, ds):
        return [p for p in self.pages.values() if p["ds"] == ds and not p["trash"]]


def _readable(props):
    """Give written properties the plain_text fields Notion returns when reading."""
    out = {}
    for name, value in props.items():
        key = next(iter(value))
        if key in ("title", "rich_text"):
            value = {"type": key, key: [{"plain_text": t["text"]["content"]} for t in value[key]]}
        else:
            value = {"type": key, **value}
        out[name] = value
    return out


def _matches(page, f):
    if not f:
        return True
    if "and" in f:
        return all(_matches(page, sub) for sub in f["and"])
    if "or" in f:
        return any(_matches(page, sub) for sub in f["or"])
    prop = page["properties"].get(f["property"], {})
    if "checkbox" in f:
        return prop.get("checkbox", False) == f["checkbox"]["equals"]
    if "select" in f:
        return (prop.get("select") or {}).get("name") == f["select"]["equals"]
    if "number" in f:
        return prop.get("number") is not None
    if "date" in f:
        d = (prop.get("date") or {}).get("start", "")
        cond = f["date"]
        return d >= cond.get("on_or_after", "") and d <= cond.get("on_or_before", "9999")
    raise AssertionError(f)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 1, 9, 30)

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path):
    notion = FakeNotion()
    bot = Bot(Database(str(tmp_path / "t.db")), notion, "D", "S", "C", "I", "L", "NT$",
              now=Clock())
    return bot, notion


def body_text(page):
    return [b[b["type"]]["rich_text"][0]["text"]["content"] if b[b["type"]]["rich_text"] else ""
            for b in page["children"]]


def title_of(page):
    return page["properties"]["Title"]["title"][0]["plain_text"]


def texts(messages):
    return "\n".join(m["text"] for m in messages)


def quick_data(messages):
    return [i["action"]["data"] for i in messages[-1].get("quickReply", {}).get("items", [])]


def test_plain_text_becomes_diary_with_move_buttons(env):
    bot, notion = env
    out = bot.handle_text("Helped mom in the garden. Tired but happy")
    [page] = notion.live("D")
    assert page["properties"]["Kind"]["select"]["name"] == "Diary"
    assert page["properties"]["Tags"]["multi_select"] == [{"name": "family"}]
    assert page["properties"]["Date"]["date"]["start"] == "2026-11-01"
    assert body_text(page) == ["🕑 09:30", "Helped mom in the garden. Tired but happy"]
    assert title_of(page) == "Helped mom in the garden."
    assert "2. A mixed day with family" in texts(out)
    assert "3. Sunday, 1 Nov 2026" in texts(out)
    assert quick_data(out) == ["title=0", "title=1", "title=2",
                               "move=idea", "move=shop", "move=cook", "undo"]


def test_explicit_diary_has_no_move_buttons(env):
    bot, _ = env
    assert "move=shop" not in quick_data(bot.handle_text("diary: good day"))


def test_same_day_appends_next_day_new_page(env):
    bot, notion = env
    bot.handle_text("diary: Morning walk, happy")
    bot.now.now += timedelta(hours=5)
    out = bot.handle_text("share @Mei told me about church camp")
    [page] = notion.live("D")
    assert body_text(page) == ["🕑 09:30", "Morning walk, happy",
                               "🕑 14:30 · Sharing", "@Mei told me about church camp"]
    assert page["properties"]["People"]["multi_select"] == [{"name": "Mei"}]
    assert page["properties"]["Tags"]["multi_select"] == [{"name": "church"}]
    assert "Added to today's page (2 entries)" in texts(out)
    assert quick_data(out) == ["new", "undo"]

    bot.handle_text("undo")  # removes only the second entry
    assert body_text(page) == ["🕑 09:30", "Morning walk, happy"]
    assert page["properties"]["People"]["multi_select"] == []

    bot.now.now += timedelta(days=1)
    bot.handle_text("diary: new day")
    assert len(notion.live("D")) == 2


def test_new_page_and_titles(env):
    bot, notion = env
    bot.handle_text("diary: first")
    bot.handle_postback("new")
    bot.handle_text("diary: Big talk with my boss")
    first, second = notion.live("D")
    assert body_text(second)[1] == "Big talk with my boss"
    assert texts(bot.handle_postback("title=1")) == "✏️ Title: Today at work"
    assert title_of(second) == "Today at work"
    bot.handle_text("title Promotion day!")
    assert title_of(second) == "Promotion day!"
    assert title_of(first) == "First"
    bot.handle_text("diary: later")  # goes to the newest page
    assert body_text(second)[-1] == "later"


def test_reflection_flow(env):
    bot, notion = env
    out = texts(bot.handle_text("sos"))
    assert "(1/6) 🌧 What happened?" in out
    bot.handle_text("Argued with my brother")
    assert "(3/6)" in texts(bot.handle_text("angry and sad, 7"))
    bot.handle_text("skip")  # cook_skip keyword is an answer command here
    bot.handle_postback("rskip")
    out = bot.handle_text("selesai")
    [page] = notion.live("D")
    assert page["properties"]["Kind"]["select"]["name"] == "Reflection"
    assert page["properties"]["Mood"]["select"]["name"] == "😞 Low"
    assert page["properties"]["Tags"]["multi_select"] == [{"name": "family"}]
    assert body_text(page) == ["🌧 What happened?", "Argued with my brother",
                               REFLECT_Q2, "angry and sad, 7"]
    assert "Reflection saved (2 answers)" in texts(out)
    bot.handle_postback("title=0")
    assert title_of(page) == "Argued with my brother"
    # Normal messages work again afterwards.
    assert "Added 1 item" in texts(bot.handle_text("buy eggs"))


def test_reflection_with_text_cancel_and_timeout(env):
    bot, notion = env
    out = texts(bot.handle_text("refleksi: Kehilangan pekerjaan"))
    assert "(2/6)" in out
    assert len(notion.live("D")) == 1

    bot.now.now += timedelta(hours=4)  # abandoned: the next message is ordinary again
    assert "New page for today" in texts(bot.handle_text("diary: back"))

    bot.handle_text("反思")
    assert "Nothing was saved" in texts(bot.handle_text("done"))
    assert len(notion.live("D")) == 2


def test_move_diary_to_shopping(env):
    bot, notion = env
    # Moving the day's first entry removes the page...
    bot.handle_text("eggs, milk")
    out = bot.handle_postback("move=shop")
    assert notion.live("D") == []
    assert [p["properties"]["Item"]["title"][0]["plain_text"] for p in notion.live("S")] == ["Eggs", "Milk"]
    assert "Added 2 items" in texts(out)
    # ...moving a later entry removes only that entry.
    bot.handle_text("diary: morning")
    bot.handle_text("solar dryer for durian")
    bot.handle_postback("move=idea")
    assert body_text(notion.live("D")[0]) == ["🕑 09:30", "morning"]
    assert len(notion.live("I")) == 1


def test_shopping_add_list_bought_undo(env):
    bot, notion = env
    out = bot.handle_text("buy eggs 10, 2 milk, dish soap")
    assert "Eggs ×10 (Dairy & Eggs)" in texts(out)
    listing = texts(bot.handle_text("list"))
    assert "To buy (3)" in listing and "Household" in listing

    out = texts(bot.handle_text("bought eggs, bananas"))
    assert "Ticked: Eggs" in out and "Not on the list: bananas" in out and "2 left" in out
    bot.handle_text("undo")
    assert "To buy (3)" in texts(bot.handle_text("list"))

    bot.handle_text("bought all")
    assert "Nothing to buy" in texts(bot.handle_text("list"))


def test_cook_add_ingredients_menu_and_done(env):
    bot, notion = env
    out = bot.handle_text("cook fried rice tue dinner with rice, eggs")
    assert "Tue 11/3 · Dinner" in texts(out)
    ingr = [d for d in quick_data(out) if d.startswith("ingr=")][0]

    out = bot.handle_postback(ingr)
    items = notion.live("S")
    assert len(items) == 2
    assert items[0]["properties"]["For meal"]["relation"] == [{"id": ingr[5:]}]

    bot.handle_text("cook pancakes today breakfast")
    menu = texts(bot.handle_text("menu"))
    assert menu.index("Sun 11/1") < menu.index("Tue 11/3")
    assert "Breakfast · Pancakes" in menu

    assert "Cooked" in texts(bot.handle_text("cooked fried rice"))
    assert "Fried rice ✅" in texts(bot.handle_text("menu"))
    assert "No meal planned for today" not in texts(bot.handle_text("cooked"))  # pancakes


def test_undo_nothing(env):
    bot, _ = env
    assert texts(bot.handle_text("undo")) == "Nothing to undo."


def test_notion_errors_are_reported(env):
    bot, notion = env
    notion.fail = True
    assert "Couldn't reach Notion: 503: down" in texts(bot.handle_text("buy eggs"))


def test_without_notion(tmp_path):
    bot = Bot(Database(str(tmp_path / "t.db")), None)
    assert "run.py --notion" in texts(bot.handle_text("hello"))
    assert "Shopping" in texts(bot.handle_text("help"))


def test_ideas_add_list_move_undo(env):
    bot, notion = env
    out = bot.handle_text("idea sell durian chips #farm")
    [page] = notion.live("I")
    assert page["properties"]["Status"]["select"]["name"] == "💡 New"
    assert "#business #farm" in texts(out)
    assert quick_data(out) == ["ideas", "undo"]

    bot.handle_text("a bot that waters plants")
    bot.handle_postback("move=idea")
    assert notion.live("D") == []
    listing = texts(bot.handle_text("ideas"))
    assert "Open ideas (2)" in listing and "Sell durian chips (11/1) #business #farm" in listing

    notion.update_page(page["id"], {"Status": {"select": {"name": "✅ Done"}}})
    assert "Open ideas (1)" in texts(bot.handle_text("ideas"))
    assert "idea removed" in texts(bot.handle_text("undo"))
    assert "No open ideas" in texts(bot.handle_text("ideas"))


def test_bought_with_prices_and_history(env):
    bot, notion = env
    bot.handle_text("buy eggs 10, milk")
    out = texts(bot.handle_text("bought eggs 65, 2 milk 90, soap 45, bread"))
    assert "Ticked: Eggs NT$65, Milk NT$90" in out
    assert "Price saved: Soap NT$45" in out       # wasn't on the list
    assert "Not on the list: bread" in out
    eggs = next(p for p in notion.live("S") if p["properties"]["Item"]["title"][0]["plain_text"] == "Eggs")
    assert eggs["properties"]["Price"]["number"] == 65
    assert eggs["properties"]["Bought on"]["date"]["start"] == "2026-11-01"

    bot.handle_text("undo")
    assert eggs["properties"]["Price"]["number"] is None
    assert len(notion.live("S")) == 2               # the soap record is gone
    bot.handle_text("bought eggs 65")

    bot.now.now += timedelta(days=7)
    out = texts(bot.handle_text("price eggs 72"))
    assert out == "💰 Saved: Eggs NT$72\nLast time: NT$65 on 2026/11/1"
    bot.handle_text("price telur 25rb")
    out = texts(bot.handle_text("harga eggs"))
    assert "Eggs: 2 prices" in out
    assert out.index("2026/11/8") < out.index("2026/11/1")
    assert "Lowest NT$65 · Highest NT$72 · Average NT$68.5" in out
    assert "No prices recorded for Bread" in texts(bot.handle_text("price bread"))


def test_saved_lists(env):
    bot, notion = env
    assert "No saved lists" in texts(bot.handle_text("lists"))
    out = bot.handle_text("save list weekly: eggs 10, milk, rice 2kg")
    assert "Saved list 'weekly' (3 items)" in texts(out)
    assert quick_data(out) == ["use=weekly"]
    bot.handle_text("save list Weekly：eggs 10, milk, rice 2kg, bananas")  # updates, not duplicates
    assert len(notion.live("L")) == 1

    bot.handle_text("buy milk")
    out = texts(bot.handle_text("use list week"))
    assert "Added 3 from 'weekly'" in out and "Already on the list: Milk" in out
    assert "Rice ×2 kg" in out
    assert len(notion.live("S")) == 4
    bot.handle_text("undo")
    assert len(notion.live("S")) == 1

    assert quick_data(bot.handle_text("我的清單")) == ["use=weekly"]
    assert "Added 3" in texts(bot.handle_postback("use=weekly"))
    assert "No saved list called 'party'" in texts(bot.handle_text("use list party"))
