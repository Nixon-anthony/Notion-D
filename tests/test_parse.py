from datetime import date

import pytest

from app.parse import Item, classify, parse_diary, parse_idea, parse_item, parse_items, parse_meal

SUN = date(2026, 11, 1)  # a Sunday


@pytest.mark.parametrize("text, action, body", [
    ("buy eggs, milk", "shop_add", "eggs, milk"),
    ("b: eggs", "shop_add", "eggs"),
    ("beli telur 10 butir", "shop_add", "telur 10 butir"),
    ("買雞蛋、牛奶", "shop_add", "雞蛋、牛奶"),
    ("need dish soap", "shop_add", "dish soap"),
    ("bought eggs", "shop_bought", "eggs"),
    ("sudah beli telur", "shop_bought", "telur"),
    ("買了雞蛋", "shop_bought", "雞蛋"),
    ("cook fried rice", "cook_add", "fried rice"),
    ("masak nasi goreng besok", "cook_add", "nasi goreng besok"),
    ("煮番茄炒蛋", "cook_add", "番茄炒蛋"),
    ("cooked fried rice", "cook_done", "fried rice"),
    ("skip pasta", "cook_skip", "pasta"),
    ("list", "shop_list", ""),
    ("清單", "shop_list", ""),
    ("Menu", "cook_menu", ""),
    ("undo", "undo", ""),
    ("help", "help", ""),
    ("idea solar dryer", "idea_add", "solar dryer"),
    ("ide: jual pupuk", "idea_add", "jual pupuk"),
    ("💡 farm app", "idea_add", "farm app"),
    ("想法：做一個網站", "idea_add", "做一個網站"),
    ("ideas", "idea_list", ""),
    ("點子清單", "idea_list", ""),
    ("sos", "reflect", ""),
    ("reflect: lost my job", "reflect", "lost my job"),
    ("反思今天的事", "reflect", "今天的事"),
    ("new", "new_page", ""),
    ("price eggs", "price", "eggs"),
    ("harga telur", "price", "telur"),
    ("價格 雞蛋", "price", "雞蛋"),
    ("save list weekly: eggs", "list_save", "weekly: eggs"),
    ("use list weekly", "list_use", "weekly"),
    ("lists", "list_show", ""),
    ("title A quiet Sunday", "set_title", "A quiet Sunday"),
    ("標題：安靜的主日", "set_title", "安靜的主日"),
    ("diary: long day", "diary", "long day"),
    ("日記 今天很累", "diary", "今天很累"),
])
def test_classify(text, action, body):
    cmd = classify(text)
    assert (cmd.action, cmd.body) == (action, body)
    assert cmd.explicit


@pytest.mark.parametrize("text", [
    "Cookies with mom today, so happy",   # "cook" must be a whole word
    "Listening to music tonight",          # "list" only as the whole message
    "Bought? No, I made it myself",        # punctuation after a trigger isn't a separator
    "Today was long",
])
def test_unmatched_falls_back_to_diary(text):
    cmd = classify(text)
    assert cmd.action == "diary" and cmd.body == text and not cmd.explicit


def test_diary_kinds():
    assert classify("share God was faithful").kind == "Sharing"
    assert classify("share God was faithful").body == "God was faithful"
    # Mood-like triggers keep the full text.
    cmd = classify("grateful for my wife")
    assert cmd.kind == "Gratitude" and cmd.body == "grateful for my wife"
    # "identity…" isn't an idea
    assert classify("identity crisis today").action == "diary"


@pytest.mark.parametrize("text, item", [
    ("eggs", Item("Eggs", None, "", "Dairy & Eggs")),
    ("2 milk", Item("Milk", 2, "", "Dairy & Eggs")),
    ("milk x2", Item("Milk", 2, "", "Dairy & Eggs")),
    ("2kg rice", Item("Rice", 2, "kg", "Pantry")),
    ("rice 2 kg", Item("Rice", 2, "kg", "Pantry")),
    ("2 lemons", Item("Lemons", 2, "", "Produce")),
    ("telur ayam 10 butir", Item("Telur ayam", 10, "butir", "Dairy & Eggs")),
    ("雞蛋10顆", Item("雞蛋", 10, "顆", "Dairy & Eggs")),
    ("10顆雞蛋", Item("雞蛋", 10, "顆", "Dairy & Eggs")),
    ("dish soap", Item("Dish soap", None, "", "Household")),
    ("pasta gigi", Item("Pasta gigi", None, "", "Household")),
    ("toilet paper", Item("Toilet paper", None, "", "Household")),
    ("eggplant", Item("Eggplant", None, "", "Produce")),
    ("7up", Item("7up", None, "", "Other")),
    ("chicken thighs 1.5kg", Item("Chicken thighs", 1.5, "kg", "Meat & Fish")),
])
def test_parse_item(text, item):
    assert parse_item(text) == item


def test_parse_items_splits_languages():
    assert [i.name for i in parse_items("eggs, 2 milk and bread")] == ["Eggs", "Milk", "Bread"]
    assert [i.name for i in parse_items("telur dan susu")] == ["Telur", "Susu"]
    assert [i.name for i in parse_items("雞蛋、牛奶，麵包")] == ["雞蛋", "牛奶", "麵包"]


@pytest.mark.parametrize("text, dish, day, meal, ingredients", [
    ("fried rice tue dinner with rice, eggs, scallion", "Fried rice", date(2026, 11, 3),
     "Dinner", ["rice", "eggs", "scallion"]),
    ("fried rice for dinner tomorrow", "Fried rice", date(2026, 11, 2), "Dinner", []),
    ("pancakes", "Pancakes", SUN, None, []),
    ("pancakes sunday breakfast", "Pancakes", SUN, "Breakfast", []),
    ("nasi goreng besok makan malam pakai nasi, telur", "Nasi goreng", date(2026, 11, 2),
     "Dinner", ["nasi", "telur"]),
    ("番茄炒蛋 明天晚餐 用番茄、蛋", "番茄炒蛋", date(2026, 11, 2), "Dinner", ["番茄", "蛋"]),
    ("咖哩飯週三午餐", "咖哩飯", date(2026, 11, 4), "Lunch", []),
    ("soup 11/5", "Soup", date(2026, 11, 5), None, []),
    ("soup 1/5", "Soup", date(2027, 1, 5), None, []),
])
def test_parse_meal(text, dish, day, meal, ingredients):
    plan = parse_meal(text, SUN)
    assert (plan.dish, plan.date, plan.meal, plan.ingredients) == (dish, day, meal, ingredients)


def test_parse_diary():
    e = parse_diary("Helped mom in the garden. Tired but happy!")
    assert e.title == "Helped mom in the garden."
    assert e.mood == "😐 Okay"
    assert e.tags == ["family"]

    e = parse_diary("今天去教會，好開心 @Mei")
    assert e.mood == "😊 Good" and e.tags == ["church"] and e.people == ["Mei"]

    e = parse_diary("Capek banget, kerja lembur sampai malam")
    assert e.mood == "😞 Low" and e.tags == ["work"]

    assert parse_diary("quiet day #rest").tags == ["rest"]

    long = "x" * 80
    assert parse_diary(long).title == "x" * 50 + "…"


def test_parse_idea():
    i = parse_idea("Sell durian chips at the market #farm")
    assert i.title == "Sell durian chips at the market"
    assert i.tags == ["business", "farm"]
    assert parse_idea("做一個記帳 app 給教會").tags == ["tech", "church"]


def test_suggest_titles():
    from app.parse import suggest_titles
    assert suggest_titles("Long day at the farm. Hot.", SUN, "low", ["farm"]) == [
        "Long day at the farm.", "A heavy day on the farm", "Sunday, 1 Nov 2026"]
    assert suggest_titles("hmm", SUN, None, [], prefix="Reflection") == [
        "Hmm", "Reflection: today", "Reflection · Sunday, 1 Nov 2026"]


@pytest.mark.parametrize("text, name, qty, price", [
    ("eggs 65", "Eggs", None, 65),
    ("2 milk 90", "Milk", 2, 90),
    ("2 milk $90", "Milk", 2, 90),
    ("雞蛋65元", "雞蛋", None, 65),
    ("10顆雞蛋 80", "雞蛋", 10, 80),
    ("telur 25rb", "Telur", None, 25000),
    ("eggs @ 65.5", "Eggs", None, 65.5),
    ("rice 2kg", "Rice", 2, None),   # unit -> quantity, not price
    ("milk x2", "Milk", 2, None),
    ("eggs", "Eggs", None, None),
])
def test_parse_priced(text, name, qty, price):
    from app.parse import parse_priced
    item, got = parse_priced(text)
    assert (item.name, item.qty, got) == (name, qty, price)
