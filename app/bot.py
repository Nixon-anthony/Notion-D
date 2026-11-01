"""What the bot does with each message. Returns LINE message dicts to reply with."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta

from app import notion as n
from app.db import Database
from app.notion import Notion, NotionError
from app.parse import (MOODS, Command, Item, classify, detect_tags, mood_counts, mood_key,
                       parse_diary, parse_idea, parse_item, parse_items, parse_meal, parse_priced,
                       split_items,
                       suggest_titles)

log = logging.getLogger("diary_bot")

HELP_TEXT = """📔 Diary — just type. Everything you write today goes on today's page.
  share … / grateful … sets the kind. @Name adds a person, #word a tag.
  new — next message starts a new page · title … — rename the page
🕊 Reflection — sos / reflect / refleksi / 反思
  I ask a few questions one at a time; skip or done any time.
💡 Ideas
  idea solar dryer for the farm #farm   (also: ide / 想法 / 💡)
  ideas  — your open ideas
🛒 Shopping
  buy eggs 10, 2 milk, dish soap   (also: b: / beli / 買)
  list   — what's left to buy
  bought eggs 65, milk 90   — ticks off and saves prices (or: bought all)
  price eggs   — price history · price eggs 65 — save a price you saw
📝 Saved lists
  save list weekly: eggs 10, milk, rice
  use list weekly — adds it to the shopping list · lists — show all
🍳 Cooking
  cook fried rice tue dinner with rice, eggs, scallion   (also: c: / masak / 煮)
  menu   — the next 7 days
  cooked fried rice / skip fried rice
↩️ undo — removes what I just saved"""

WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def text_msg(text: str, quick: list[tuple[str, str]] | None = None) -> dict:
    msg: dict = {"type": "text", "text": text[:5000]}
    if quick:
        msg["quickReply"] = {"items": [
            {"type": "action", "action": {"type": "postback", "label": label[:20],
                                          "data": data, "displayText": label}}
            for label, data in quick]}
    return msg


def _fmt_qty(item: Item) -> str:
    if item.qty is None:
        return ""
    qty = int(item.qty) if float(item.qty).is_integer() else item.qty
    return f" ×{qty}{(' ' + item.unit) if item.unit else ''}"


def _fmt_date(d: date | None) -> str:
    return f"{d.year}/{d.month}/{d.day}" if d else "?"


def _fmt_day(d: date) -> str:
    return f"{WEEKDAY[d.weekday()]} {d.month}/{d.day}"


UNDO = ("↩️ Undo", "undo")

REFLECT_QUESTIONS = [
    "🌧 What happened?",
    "💭 What am I feeling right now? (name it, and how strong, 0–10)",
    "🧠 What am I telling myself about it?",
    "🤲 What do I need right now?",
    "🙏 What is still true, or what can I be thankful for?",
    "👣 One small next step I can take:",
]
REFLECT_SKIP = {"skip", "next", "lewati", "跳過", "跳过"}
REFLECT_STOP = {"done", "stop", "finish", "selesai", "berhenti", "結束", "结束", "完成"}
REFLECT_BUTTONS = [("⏭ Skip", "rskip"), ("✅ Finish", "rdone")]
REFLECT_TIMEOUT = timedelta(hours=3)


class Bot:
    def __init__(self, db: Database, notion: Notion | None, diary_ds: str = "",
                 shopping_ds: str = "", cooking_ds: str = "", ideas_ds: str = "",
                 lists_ds: str = "", currency: str = "",
                 now: Callable[[], datetime] = datetime.now):
        self.db = db
        self.notion = notion
        self.ds = {"diary": diary_ds, "shop": shopping_ds, "cook": cooking_ds, "idea": ideas_ds,
                   "lists": lists_ds}
        self.currency = currency
        self.now = now

    def today(self) -> date:
        return self.now().date()

    # --- entry points -------------------------------------------------------------

    def handle_text(self, text: str) -> list[dict]:
        cmd = classify(text)
        if cmd.action == "help":
            return [text_msg(HELP_TEXT)]
        if not self.notion:
            return [text_msg("Notion isn't connected yet. Run: python3 run.py --notion")]
        if state := self._reflecting():  # during a reflection every message is an answer
            try:
                return self._reflect_answer(state, text)
            except NotionError as e:
                log.warning("notion error: %s", e)
                return [text_msg(f"⚠️ Couldn't reach Notion: {e}\nSend your answer again.")]
        if not cmd.body and cmd.action in ("diary", "idea_add", "shop_add", "shop_bought", "cook_add"):
            return [text_msg("Type something after the keyword. Send 'help' for examples.")]
        try:
            return getattr(self, cmd.action)(cmd)
        except NotionError as e:
            log.warning("notion error: %s", e)
            return [text_msg(f"⚠️ Couldn't reach Notion: {e}")]

    def handle_postback(self, data: str) -> list[dict]:
        if not self.notion:
            return [text_msg("Notion isn't connected yet. Run: python3 run.py --notion")]
        key, _, value = data.partition("=")
        try:
            if key == "undo":
                return self.undo(None)
            if key == "list":
                return self.shop_list(None)
            if key == "menu":
                return self.cook_menu(None)
            if key == "ideas":
                return self.idea_list(None)
            if key == "new":
                return self.new_page(None)
            if key == "title":
                return self._pick_title(value)
            if key in ("rskip", "rdone"):
                state = self._reflecting()
                if not state:
                    return [text_msg("No reflection in progress. Start one with: sos")]
                return self._reflect_answer(state, "skip" if key == "rskip" else "done")
            if key == "move":
                return self._move(value)
            if key == "use":
                return self._use_list(value)
            if key == "ingr":
                return self._add_ingredients(value)
        except NotionError as e:
            log.warning("notion error: %s", e)
            return [text_msg(f"⚠️ Couldn't reach Notion: {e}")]
        return []

    def _remember(self, kind: str, pages: list[str], **extra) -> None:
        self.db.set("last", {"kind": kind, "pages": pages, **extra})

    # --- diary ----------------------------------------------------------------------

    # One page per day: the first entry creates it, later ones are added under a time
    # heading. State in the local db under "day": {date, page, title, tags, people,
    # good, low, entries}.

    def _entry_blocks(self, text: str, kind: str) -> list[dict]:
        label = f"🕑 {self.now():%H:%M}" + (f" · {kind}" if kind != "Diary" else "")
        return [n.heading(label)] + n.paragraphs(text)

    def _details(self, mood: str | None, tags: list[str], people: list[str]) -> str:
        return " · ".join(filter(None, [mood, " ".join(f"#{t}" for t in tags),
                                        " ".join(f"@{p}" for p in people)]))

    def _title_choices(self, page: str, options: list[str]) -> tuple[str, list[tuple[str, str]]]:
        self.db.set("titles", {"page": page, "options": options})
        text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(options))
        buttons = [(f"{i + 1} · {t}", f"title={i}") for i, t in enumerate(options)]
        return f"Pick a title, or send: title …\n{text}", buttons

    def diary(self, cmd) -> list[dict]:
        entry = parse_diary(cmd.body, cmd.kind)
        day = self.db.get("day")
        force_new = self.db.get("force_new")
        if day and day["date"] == self.today().isoformat() and not force_new:
            return self._append_entry(day, entry, cmd)
        return self._new_day_page(day, entry, cmd)

    def _move_buttons(self, cmd) -> list[tuple[str, str]]:
        if cmd.explicit:
            return []
        # Saved as diary only because no keyword matched.
        return [("💡 Idea instead", "move=idea"), ("🛒 Shopping instead", "move=shop"),
                ("🍳 Cooking instead", "move=cook")]

    def _new_day_page(self, prev_day: dict | None, entry, cmd) -> list[dict]:
        good, low = mood_counts(entry.text)
        options = suggest_titles(entry.text, self.today(), mood_key(good, low), entry.tags)
        props = {
            "Title": n.title(options[0]),
            "Date": n.date_prop(self.today()),
            "Kind": n.select(entry.kind),
            "Tags": n.multi_select(entry.tags),
            "People": n.multi_select(entry.people),
            "Mood": n.select(entry.mood),
        }
        page = self.notion.create_page(self.ds["diary"], props,
                                       self._entry_blocks(entry.text, entry.kind))
        self.db.set("force_new", None)
        self.db.set("day", {"date": self.today().isoformat(), "page": page, "title": options[0],
                            "tags": entry.tags, "people": entry.people, "good": good,
                            "low": low, "entries": 1})
        self._remember("diary_page", [page], text=cmd.body, prev_day=prev_day)
        choose, buttons = self._title_choices(page, options)
        details = self._details(entry.mood, entry.tags, entry.people)
        return [text_msg(f"📔 New page for today\n{details}\n\n{choose}".replace("\n\n\n", "\n\n"),
                         buttons + self._move_buttons(cmd) + [UNDO])]

    def _append_entry(self, day: dict, entry, cmd) -> list[dict]:
        blocks = self.notion.append_blocks(day["page"], self._entry_blocks(entry.text, entry.kind))
        good, low = mood_counts(entry.text)
        prev = dict(day)
        day["good"] += good
        day["low"] += low
        day["tags"] = list(dict.fromkeys(day["tags"] + entry.tags))
        day["people"] = list(dict.fromkeys(day["people"] + entry.people))
        day["entries"] += 1
        key = mood_key(day["good"], day["low"])
        mood = MOODS[key] if key else None
        self.notion.update_page(day["page"], {
            "Tags": n.multi_select(day["tags"]), "People": n.multi_select(day["people"]),
            "Mood": n.select(mood)})
        self.db.set("day", day)
        self._remember("diary_append", blocks, text=cmd.body, prev_day=prev)
        details = self._details(mood, day["tags"], day["people"])
        return [text_msg(f"📔 Added to today's page ({day['entries']} entries)\n{day['title']}"
                         + (f"\n{details}" if details else ""),
                         self._move_buttons(cmd) + [("📄 New page", "new"), UNDO])]

    def new_page(self, _cmd) -> list[dict]:
        self.db.set("force_new", True)
        return [text_msg("📄 OK. Your next diary message starts a new page for today.")]

    def set_title(self, cmd) -> list[dict]:
        target = self.db.get("titles") or {}
        day = self.db.get("day") or {}
        page = target.get("page") or day.get("page")
        if not page:
            return [text_msg("No page to rename yet. Write a diary entry first.")]
        return self._rename(page, cmd.body.strip())

    def _pick_title(self, index: str) -> list[dict]:
        target = self.db.get("titles")
        try:
            title = target["options"][int(index)]
        except (TypeError, KeyError, IndexError, ValueError):
            return [text_msg("That title choice expired. Send: title …")]
        return self._rename(target["page"], title)

    def _rename(self, page: str, title: str) -> list[dict]:
        if not title:
            return [text_msg("Send the title after the word, e.g. title A quiet Sunday")]
        self.notion.update_page(page, {"Title": n.title(title)})
        day = self.db.get("day")
        if day and day["page"] == page:
            day["title"] = title
            self.db.set("day", day)
        return [text_msg(f"✏️ Title: {title}")]

    # --- emergency reflection -----------------------------------------------------------

    def _reflecting(self) -> dict | None:
        state = self.db.get("reflect")
        if state and self.now() - datetime.fromisoformat(state["started"]) > REFLECT_TIMEOUT:
            self.db.set("reflect", None)  # abandoned; the answers so far stay in Notion
            return None
        return state

    def reflect(self, cmd) -> list[dict]:
        now = self.now()
        title = f"Reflection · {_fmt_day(now.date())} {now:%H:%M}"
        page = self.notion.create_page(self.ds["diary"], {
            "Title": n.title(title), "Date": n.date_prop(now.date()),
            "Kind": n.select("Reflection")})
        state = {"page": page, "step": 0, "started": now.isoformat(), "answers": []}
        self.db.set("reflect", state)
        self._remember("reflection", [page])
        intro = "🕊 Reflection started. Take your time; answer in any language.\n"
        if cmd.body:
            return self._reflect_answer(state, cmd.body, intro)
        return [text_msg(f"{intro}\n(1/{len(REFLECT_QUESTIONS)}) {REFLECT_QUESTIONS[0]}",
                         REFLECT_BUTTONS)]

    def _reflect_answer(self, state: dict, text: str, intro: str = "") -> list[dict]:
        word = text.strip().lower()
        if word in REFLECT_STOP:
            return self._reflect_finish(state)
        if word not in REFLECT_SKIP:
            self.notion.append_blocks(
                state["page"], [n.heading(REFLECT_QUESTIONS[state["step"]], 2)] + n.paragraphs(text))
            state["answers"].append(text)
        state["step"] += 1
        if state["step"] >= len(REFLECT_QUESTIONS):
            return self._reflect_finish(state)
        self.db.set("reflect", state)
        q = REFLECT_QUESTIONS[state["step"]]
        return [text_msg(f"{intro}({state['step'] + 1}/{len(REFLECT_QUESTIONS)}) {q}",
                         REFLECT_BUTTONS)]

    def _reflect_finish(self, state: dict) -> list[dict]:
        self.db.set("reflect", None)
        if not state["answers"]:
            self.notion.trash(state["page"])
            self.db.set("last", None)
            return [text_msg("🕊 Reflection closed. Nothing was saved.")]
        text = "\n".join(state["answers"])
        good, low = mood_counts(text)
        key = mood_key(good, low)
        tags = detect_tags(text)
        self.notion.update_page(state["page"], {
            "Mood": n.select(MOODS[key] if key else None), "Tags": n.multi_select(tags)})
        options = suggest_titles(state["answers"][0], self.today(), key, tags, prefix="Reflection")
        choose, buttons = self._title_choices(state["page"], options)
        return [text_msg(f"🕊 Reflection saved ({len(state['answers'])} answers). "
                         f"Well done for writing it down.\n\n{choose}", buttons + [UNDO])]

    def _move(self, target: str) -> list[dict]:
        last = self.db.get("last")
        actions = {"idea": "idea_add", "shop": "shop_add", "cook": "cook_add"}
        if (not last or last.get("kind") not in ("diary_page", "diary_append")
                or target not in actions):
            return [text_msg("Nothing to move. Start the message with idea …, buy … or cook … instead.")]
        self._revert(last)
        action = actions[target]
        return getattr(self, action)(Command(action, last["text"]))

    # --- ideas ------------------------------------------------------------------------

    def idea_add(self, cmd) -> list[dict]:
        idea = parse_idea(cmd.body)
        props = {
            "Idea": n.title(idea.title),
            "Date": n.date_prop(self.today()),
            "Tags": n.multi_select(idea.tags),
            "Status": n.select("💡 New"),
        }
        page = self.notion.create_page(self.ds["idea"], props, n.paragraphs(idea.text))
        self._remember("idea", [page])
        tags = " ".join(f"#{t}" for t in idea.tags)
        return [text_msg(f"💡 Saved idea\n{idea.title}" + (f"\n{tags}" if tags else ""),
                         [("💡 Ideas", "ideas"), UNDO])]

    def idea_list(self, _cmd) -> list[dict]:
        pages = self.notion.query(
            self.ds["idea"],
            filter={"or": [{"property": "Status", "select": {"equals": s}}
                           for s in ("💡 New", "🔍 Exploring")]},
            sorts=[{"property": "Date", "direction": "descending"}], limit=15)
        if not pages:
            return [text_msg("💡 No open ideas. Add one: idea …")]
        lines = []
        for p in pages:
            d = n.get_date(p, "Date")
            mark = "🔍 " if n.get_select(p, "Status") == "🔍 Exploring" else ""
            tags = " ".join(f"#{t['name']}" for t in
                            (p["properties"].get("Tags") or {}).get("multi_select", []))
            lines.append(f"• {mark}{n.plain(p, 'Idea')}"
                         + (f" ({d.month}/{d.day})" if d else "") + (f" {tags}" if tags else ""))
        return [text_msg(f"💡 Open ideas ({len(pages)})\n" + "\n".join(lines))]

    # --- shopping ---------------------------------------------------------------------

    def _create_items(self, items: list[Item], meal_page: str | None = None) -> list[str]:
        pages = []
        for item in items:
            props = {
                "Item": n.title(item.name),
                "Category": n.select(item.category),
                "Bought": n.checkbox(False),
            }
            if item.qty is not None:
                props["Qty"] = n.number(item.qty)
            if item.unit:
                props["Unit"] = n.rich_text(item.unit)
            if meal_page:
                props["For meal"] = n.relation([meal_page])
            pages.append(self.notion.create_page(self.ds["shop"], props))
        return pages

    def shop_add(self, cmd) -> list[dict]:
        items = parse_items(cmd.body)
        if not items:
            return [text_msg("I didn't find any items. Example: buy eggs 10, 2 milk")]
        pages = self._create_items(items)
        self._remember("shop", pages)
        lines = [f"• {i.name}{_fmt_qty(i)} ({i.category})" for i in items]
        noun = "item" if len(items) == 1 else "items"
        return [text_msg(f"🛒 Added {len(items)} {noun}\n" + "\n".join(lines),
                         [("📋 List", "list"), UNDO])]

    def _open_items(self) -> list[dict]:
        return self.notion.query(
            self.ds["shop"], filter={"property": "Bought", "checkbox": {"equals": False}},
            sorts=[{"property": "Category", "direction": "ascending"}])

    def shop_list(self, _cmd) -> list[dict]:
        pages = self._open_items()
        if not pages:
            return [text_msg("🛒 Nothing to buy 🎉")]
        groups: dict[str, list[str]] = {}
        for p in pages:
            item = Item(n.plain(p, "Item"), n.get_number(p, "Qty"), n.plain(p, "Unit"))
            groups.setdefault(n.get_select(p, "Category") or "Other", []).append(
                f"• {item.name}{_fmt_qty(item)}")
        body = "\n".join(f"{cat}\n" + "\n".join(lines) for cat, lines in groups.items())
        return [text_msg(f"🛒 To buy ({len(pages)})\n{body}")]

    @staticmethod
    def _same_item(a: str, b: str) -> bool:
        a, b = a.lower(), b.lower()
        return bool(a and b) and (a in b or b in a)

    def _matches(self, name: str, page: dict) -> bool:
        return self._same_item(name, n.plain(page, "Item"))

    def _money(self, price: float) -> str:
        value = int(price) if float(price).is_integer() else price
        return f"{self.currency}{value:,}"

    def shop_bought(self, cmd) -> list[dict]:
        """Tick items off. A number at the end is the price: bought eggs 65, milk 90."""
        open_items = self._open_items()
        today = n.date_prop(self.today())
        ticked, recorded, missing = [], [], []
        if cmd.body.lower() in ("all", "semua", "全部", "都買了", "都买了"):
            ticked = [(p, None) for p in open_items]
        else:
            for wanted in split_items(cmd.body):
                item, price = parse_priced(wanted)
                hits = [p for p in open_items
                        if p not in [t[0] for t in ticked] and self._matches(item.name, p)]
                if hits:
                    ticked += [(p, price) for p in hits]
                elif price is not None:  # not on the list, but keep the price
                    recorded.append(self._record_price(item, price))
                else:
                    missing.append(wanted)
        for p, price in ticked:
            props = {"Bought": n.checkbox(True), "Bought on": today}
            if price is not None:
                props["Price"] = n.number(price)
            self.notion.update_page(p["id"], props)
        if ticked or recorded:
            self._remember("bought", [p["id"] for p, _ in ticked],
                           created=[pid for pid, _ in recorded])
        lines = []
        if ticked:
            lines.append("✅ Ticked: " + ", ".join(
                n.plain(p, "Item") + (f" {self._money(price)}" if price is not None else "")
                for p, price in ticked))
        if recorded:
            lines.append("💰 Price saved: " + ", ".join(text for _, text in recorded))
        if missing:
            lines.append("❓ Not on the list: " + ", ".join(missing))
        left = len(open_items) - len(ticked)
        lines.append(f"{left} left to buy" if left else "All done 🎉")
        return [text_msg("\n".join(lines),
                         [("📋 List", "list"), UNDO] if ticked or recorded else None)]

    def _record_price(self, item: Item, price: float) -> tuple[str, str]:
        """Save a price as an already-bought row; returns (page id, description)."""
        props = {
            "Item": n.title(item.name), "Category": n.select(item.category),
            "Bought": n.checkbox(True), "Bought on": n.date_prop(self.today()),
            "Price": n.number(price),
        }
        if item.qty is not None:
            props["Qty"] = n.number(item.qty)
        if item.unit:
            props["Unit"] = n.rich_text(item.unit)
        page = self.notion.create_page(self.ds["shop"], props)
        return page, f"{item.name}{_fmt_qty(item)} {self._money(price)}"

    def price(self, cmd) -> list[dict]:
        """price eggs -> price history; price eggs 65 -> save a price you saw."""
        if not cmd.body:
            return [text_msg("Which item? e.g. price eggs   (or save one: price eggs 65)")]
        item, price = parse_priced(cmd.body)
        history = self._price_history(item.name)
        if price is not None:
            page, text = self._record_price(item, price)
            self._remember("shop", [page])
            msg = f"💰 Saved: {text}"
            if history:
                last = history[0]
                msg += (f"\nLast time: {self._money(n.get_number(last, 'Price'))}"
                        f" on {_fmt_date(n.get_date(last, 'Bought on'))}")
            return [text_msg(msg, [UNDO])]
        if not history:
            return [text_msg(f"No prices recorded for {item.name} yet.\n"
                             f"Save one: bought {item.name.lower()} 65  or  price {item.name.lower()} 65")]
        prices = [n.get_number(p, "Price") for p in history]
        lines = []
        for p in history[:8]:
            qty = Item("", n.get_number(p, "Qty"), n.plain(p, "Unit"))
            lines.append(f"• {_fmt_date(n.get_date(p, 'Bought on'))}  "
                         f"{self._money(n.get_number(p, 'Price'))}  {n.plain(p, 'Item')}{_fmt_qty(qty)}")
        avg = sum(prices) / len(prices)
        summary = (f"Lowest {self._money(min(prices))} · Highest {self._money(max(prices))}"
                   f" · Average {self._money(round(avg, 1))}") if len(prices) > 1 else ""
        return [text_msg(f"💰 {item.name}: {len(prices)} price{'s' if len(prices) > 1 else ''}\n"
                         + "\n".join(lines) + (f"\n{summary}" if summary else ""))]

    def _price_history(self, name: str) -> list[dict]:
        pages = self.notion.query(
            self.ds["shop"], filter={"property": "Price", "number": {"is_not_empty": True}},
            sorts=[{"property": "Bought on", "direction": "descending"}], limit=500)
        pages = [p for p in pages if self._matches(name, p)]
        return sorted(pages, key=lambda p: n.get_date(p, "Bought on") or date.min, reverse=True)

    # --- saved lists ------------------------------------------------------------------

    def _saved_lists(self) -> list[dict]:
        return self.notion.query(self.ds["lists"], limit=100)

    def _find_list(self, name: str) -> dict | None:
        name = name.strip().lower()
        lists = self._saved_lists()
        return (next((p for p in lists if n.plain(p, "List").lower() == name), None)
                or next((p for p in lists if name and name in n.plain(p, "List").lower()), None))

    def list_save(self, cmd) -> list[dict]:
        """save list weekly: eggs 10, milk, rice"""
        name, sep, items = cmd.body.replace("：", ":").partition(":")
        if not sep or not name.strip() or not split_items(items):
            return [text_msg("Write it like: save list weekly: eggs 10, milk, rice")]
        name, items = name.strip(), ", ".join(split_items(items))
        existing = self._find_list(name)
        if existing and n.plain(existing, "List").lower() == name.lower():
            self.notion.update_page(existing["id"], {"Items": n.rich_text(items)})
            verb = "Updated"
        else:
            self.notion.create_page(self.ds["lists"], {"List": n.title(name),
                                                       "Items": n.rich_text(items)})
            verb = "Saved"
        return [text_msg(f"📝 {verb} list '{name}' ({len(split_items(items))} items)\n{items}\n\n"
                         f"Add it to your shopping list any time: use list {name}",
                         [(f"🛒 Use {name}", f"use={name}")])]

    def list_use(self, cmd) -> list[dict]:
        return self._use_list(cmd.body)

    def _use_list(self, name: str) -> list[dict]:
        saved = self._find_list(name)
        if not saved:
            return [text_msg(f"No saved list called '{name}'. Send 'lists' to see them.")]
        open_names = [n.plain(p, "Item") for p in self._open_items()]
        items, skipped = [], []
        for item in parse_items(n.plain(saved, "Items")):
            if any(self._same_item(item.name, o) for o in open_names):
                skipped.append(item.name)
            else:
                items.append(item)
        pages = self._create_items(items)
        if pages:
            self._remember("shop", pages)
        msg = f"🛒 Added {len(items)} from '{n.plain(saved, 'List')}'"
        if items:
            msg += "\n" + "\n".join(f"• {i.name}{_fmt_qty(i)}" for i in items)
        if skipped:
            msg += "\nAlready on the list: " + ", ".join(skipped)
        return [text_msg(msg, [("📋 List", "list"), UNDO] if pages else None)]

    def list_show(self, _cmd) -> list[dict]:
        lists = self._saved_lists()
        if not lists:
            return [text_msg("No saved lists yet. Make one:\nsave list weekly: eggs 10, milk, rice")]
        lines = [f"• {n.plain(p, 'List')}: {n.plain(p, 'Items')}" for p in lists]
        buttons = [(f"🛒 {n.plain(p, 'List')}", f"use={n.plain(p, 'List')}") for p in lists[:12]]
        return [text_msg("📝 Saved lists\n" + "\n".join(lines) + "\n\nTap one to add it:", buttons)]

    # --- cooking ----------------------------------------------------------------------

    def cook_add(self, cmd) -> list[dict]:
        plan = parse_meal(cmd.body, self.today())
        if not plan.dish:
            return [text_msg("What's the dish? Example: cook fried rice tue dinner")]
        props = {
            "Dish": n.title(plan.dish),
            "Date": n.date_prop(plan.date),
            "Status": n.select("Planned"),
            "Meal": n.select(plan.meal),
        }
        if plan.ingredients:
            props["Ingredients"] = n.rich_text(", ".join(plan.ingredients))
        page = self.notion.create_page(self.ds["cook"], props)
        self._remember("cook", [page])
        when = _fmt_day(plan.date) + (f" · {plan.meal}" if plan.meal else "")
        text = f"🍳 Planned: {plan.dish}\n{when}"
        quick = [UNDO]
        if plan.ingredients:
            text += "\nIngredients: " + ", ".join(plan.ingredients)
            quick = [("🛒 Add to shopping", f"ingr={page}"), ("📅 Menu", "menu"), UNDO]
        return [text_msg(text, quick)]

    def _add_ingredients(self, page_id: str) -> list[dict]:
        page = self.notion.get_page(page_id)
        ingredients = split_items(n.plain(page, "Ingredients"))
        if not ingredients:
            return [text_msg("That meal has no ingredients listed.")]
        items = [parse_item(i) for i in ingredients]
        pages = self._create_items(items, meal_page=page_id)
        self._remember("shop", pages)
        return [text_msg(f"🛒 Added {len(items)} ingredients for {n.plain(page, 'Dish')}\n"
                         + "\n".join(f"• {i.name}{_fmt_qty(i)}" for i in items),
                         [("📋 List", "list"), UNDO])]

    def cook_menu(self, _cmd) -> list[dict]:
        start = self.today()
        end = start + timedelta(days=6)
        pages = self.notion.query(
            self.ds["cook"],
            filter={"and": [{"property": "Date", "date": {"on_or_after": start.isoformat()}},
                            {"property": "Date", "date": {"on_or_before": end.isoformat()}}]},
            sorts=[{"property": "Date", "direction": "ascending"}])
        if not pages:
            return [text_msg("📅 No meals planned for the next 7 days.\n"
                             "Add one: cook fried rice tomorrow dinner")]
        order = {"Breakfast": 0, "Lunch": 1, "Dinner": 2}
        icons = {"Cooked": " ✅", "Skipped": " ✖️"}
        pages.sort(key=lambda p: (n.get_date(p, "Date") or start,
                                  order.get(n.get_select(p, "Meal"), 3)))
        lines, current = [], None
        for p in pages:
            d = n.get_date(p, "Date")
            if d != current:
                current = d
                lines.append(_fmt_day(d) if d else "")
            meal = n.get_select(p, "Meal")
            lines.append(f"  {meal + ' · ' if meal else ''}{n.plain(p, 'Dish')}"
                         f"{icons.get(n.get_select(p, 'Status'), '')}")
        return [text_msg("📅 Next 7 days\n" + "\n".join(lines))]

    def _set_status(self, body: str, status: str) -> list[dict]:
        today = self.today()
        pages = self.notion.query(
            self.ds["cook"], filter={"property": "Status", "select": {"equals": "Planned"}})
        name = body.strip().lower()
        if name:
            pages = [p for p in pages if name in n.plain(p, "Dish").lower()
                     or n.plain(p, "Dish").lower() in name]
        else:
            pages = [p for p in pages if n.get_date(p, "Date") == today]
        if not pages:
            return [text_msg(f"No planned meal matches '{body}'." if body
                             else "No meal planned for today. Try: cooked fried rice")]
        # The one closest to today.
        page = min(pages, key=lambda p: abs(((n.get_date(p, "Date") or today) - today).days))
        self.notion.update_page(page["id"], {"Status": n.select(status)})
        self._remember("status", [page["id"]])
        icon = "✅" if status == "Cooked" else "✖️"
        return [text_msg(f"{icon} {n.plain(page, 'Dish')}: {status}", [UNDO])]

    def cook_done(self, cmd) -> list[dict]:
        return self._set_status(cmd.body, "Cooked")

    def cook_skip(self, cmd) -> list[dict]:
        return self._set_status(cmd.body, "Skipped")

    # --- undo -------------------------------------------------------------------------

    def undo(self, _cmd) -> list[dict]:
        last = self.db.get("last")
        if not last:
            return [text_msg("Nothing to undo.")]
        self._revert(last)
        self.db.set("last", None)
        what = {"diary_page": "today's page removed", "diary_append": "entry removed",
                "reflection": "reflection removed", "idea": "idea removed",
                "shop": "items removed",
                "cook": "meal removed", "bought": "items unticked",
                "status": "meal back to Planned"}[last["kind"]]
        return [text_msg(f"↩️ Undone: {what}")]

    def _revert(self, last: dict) -> None:
        kind, pages = last["kind"], last["pages"]
        if kind == "diary_append":
            for block in pages:
                self.notion.delete_block(block)
            prev = last["prev_day"]
            key = mood_key(prev["good"], prev["low"])
            self.notion.update_page(prev["page"], {
                "Tags": n.multi_select(prev["tags"]), "People": n.multi_select(prev["people"]),
                "Mood": n.select(MOODS[key] if key else None)})
            self.db.set("day", prev)
            return
        for page in last.get("created", []):
            self.notion.trash(page)
        for page in pages:
            if kind == "bought":
                self.notion.update_page(page, {"Bought": n.checkbox(False), "Price": n.number(None),
                                               "Bought on": {"date": None}})
            elif kind == "status":
                self.notion.update_page(page, {"Status": n.select("Planned")})
            else:
                self.notion.trash(page)
        if kind == "diary_page":
            self.db.set("day", last.get("prev_day"))
        if kind == "reflection":
            self.db.set("reflect", None)


def make_now(tz) -> Callable[[], datetime]:
    return lambda: datetime.now(tz).replace(tzinfo=None)
