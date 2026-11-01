"""Keyword-based parsing: which kind of message it is, and its fields.

No AI: everything here is word lists in English, Indonesian and Traditional/Simplified
Chinese. English/Indonesian words must stand alone ("cook rice", not "cookie");
Chinese words match anywhere because Chinese has no spaces.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta


def _is_ascii(word: str) -> bool:
    return word.isascii()


def _kw_pattern(words, plural: bool = False) -> re.Pattern:
    """One regex matching any of `words`, longest first."""
    parts = []
    for w in sorted(set(words), key=len, reverse=True):
        if _is_ascii(w):
            suffix = r"(?:s|es)?" if plural else ""
            parts.append(rf"(?<![a-z0-9]){re.escape(w)}{suffix}(?![a-z0-9])")
        else:
            parts.append(re.escape(w))
    return re.compile("|".join(parts), re.IGNORECASE)


# --- what kind of message -----------------------------------------------------

@dataclass(frozen=True)
class Command:
    action: str          # diary, idea_add, idea_list, shop_add, shop_list, shop_bought,
                         # cook_add, cook_menu, cook_done, cook_skip, undo, help
    body: str = ""
    kind: str = "Diary"  # diary only: Diary / Sharing / Gratitude / Idea
    explicit: bool = True  # False when no keyword matched and we fell back to diary


# (trigger, action, diary kind, strip the trigger from the saved text?)
_PREFIXES: list[tuple[str, str, str, bool]] = []


def _add(action: str, words: str, kind: str = "", strip: bool = True) -> None:
    for w in words.split("|"):
        _PREFIXES.append((w, action, kind, strip))


# Diary. Mood words like "grateful" stay in the saved text; labels like "diary:" don't.
_add("diary", "d:|diary|journal|日記|日记|diari|catatan|buku harian", "Diary")
_add("diary", "share|sharing|分享|心得|berbagi", "Sharing")
_add("diary", "grateful|thankful|gratitude|感恩|感謝|感谢|syukur|bersyukur", "Gratitude", strip=False)
# Ideas
_add("idea_add", "idea|ideas|ide|💡|想法|點子|点子|靈感|灵感")
_add("reflect", "sos|reflect|reflection|emergency|refleksi|darurat|反思|反省|緊急|紧急")
_add("set_title", "title|judul|標題|标题|題目|题目")
# Shopping
_add("shop_add", "b:|buy|need|shopping|beli|perlu beli|belanja|買|买|要買|要买|購買|购买")
_add("price", "price|prices|harga|價格|价格|價錢|价钱|多少錢|多少钱")
_add("list_save", "save list|new list|simpan daftar|daftar baru|存清單|存清单|新清單|新清单")
_add("list_use", "use list|add list|pakai daftar|用清單|用清单")
_add("shop_bought", "bought|sudah beli|udah beli|dibeli|買了|买了|買好了|买好了|已買|已买")
# Cooking
_add("cook_add", "c:|cook|masak|煮|做菜|料理")
_add("cook_done", "cooked|sudah masak|udah masak|煮好了|煮了|做好了")
_add("cook_skip", "skip|batal masak|不煮了|不煮")

# Whole-message commands (only when the message is exactly this).
_EXACT = {
    "idea_list": "ideas|my ideas|daftar ide|ide-ide|想法清單|想法清单|點子清單|点子清单",
    "list_show": "lists|my lists|saved lists|daftar saya|semua daftar|我的清單|我的清单|清單列表|清单列表",
    "shop_list": "list|shopping list|daftar|daftar belanja|清單|清单|購物清單|购物清单|要買什麼|要买什么",
    "cook_menu": "menu|meals|menu minggu ini|菜單|菜单|這週菜單|这周菜单",
    "new_page": "new|new page|halaman baru|baru|新頁|新页|新頁面|新页面",
    "undo": "undo|batal|復原|复原|撤銷|撤销|取消",
    "help": "help|?|？|bantuan|幫助|帮助|說明|说明",
}
_EXACT_MAP = {w: action for action, words in _EXACT.items() for w in words.split("|")}

_SEPARATORS = " :：,，、-"


def classify(text: str) -> Command:
    stripped = text.strip()
    lower = stripped.lower()
    if lower in _EXACT_MAP:
        return Command(_EXACT_MAP[lower])
    for trigger, action, kind, strip in sorted(_PREFIXES, key=lambda p: len(p[0]), reverse=True):
        if not lower.startswith(trigger):
            continue
        rest = stripped[len(trigger):]
        # "cook rice" yes, "cookie" no; Chinese ("買雞蛋") needs no space.
        if _is_ascii(trigger) and not trigger.endswith(":") and rest and rest[0] not in _SEPARATORS:
            continue
        body = rest.lstrip(_SEPARATORS) if strip else stripped
        return Command(action, body, kind or "Diary")
    return Command("diary", stripped, "Diary", explicit=False)


# --- shopping -----------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    name: str
    qty: float | None = None
    unit: str = ""
    category: str = "Other"


_UNITS = ("kg|g|gr|l|ml|pcs|pc|pack|packs|box|boxes|bottle|bottles|can|cans|dozen|bag|bags|"
          "butir|bungkus|botol|kotak|ikat|sisir|kaleng|biji|"
          "顆|颗|個|个|包|瓶|盒|斤|公斤|罐|把|袋|條|条|片")
_UNIT_RE = "|".join(sorted(_UNITS.split("|"), key=len, reverse=True))
_NUM = r"(\d+(?:\.\d+)?)"
_LEADING = re.compile(rf"^{_NUM}(?:\s*({_UNIT_RE})(?![a-z]))?(?:\s+|(?=[^\x00-\x7f]))(.+)$", re.I)
_TRAILING = re.compile(rf"^(.*?\D)\s*(?:[x×*]\s*)?{_NUM}\s*({_UNIT_RE})?$", re.I)
_ITEM_SPLIT = re.compile(r"\s*(?:,|，|、|;|；|\n|\s+and\s+|\s+dan\s+|和)\s*", re.I)

CATEGORY_WORDS = {
    "Produce": "apple|banana|tomato|tomatoes|onion|garlic|potato|potatoes|carrot|cabbage|lettuce|"
               "spinach|vegetable|veggie|fruit|lemon|lime|chili|chilli|ginger|scallion|cucumber|"
               "mushroom|broccoli|corn|avocado|mango|papaya|eggplant|"
               "sayur|sayuran|buah|apel|pisang|tomat|bawang|kentang|wortel|kol|kubis|bayam|cabai|"
               "cabe|jahe|timun|jamur|jagung|jeruk|terong|"
               "青菜|蔬菜|蔥|葱|蒜|薑|姜|番茄|洋蔥|洋葱|馬鈴薯|马铃薯|土豆|紅蘿蔔|胡萝卜|高麗菜|"
               "白菜|水果|蘋果|苹果|香蕉|檸檬|柠檬|辣椒|香菇|菇|玉米|芒果|茄子",
    "Meat & Fish": "chicken|beef|pork|fish|shrimp|prawn|meat|bacon|sausage|salmon|tuna|"
                   "ayam|sapi|daging|babi|ikan|udang|sosis|"
                   "雞肉|鸡肉|雞腿|鸡腿|牛肉|豬肉|猪肉|魚|鱼|蝦|虾|肉|香腸|香肠",
    "Dairy & Eggs": "milk|egg|cheese|butter|yogurt|yoghurt|cream|"
                    "susu|telur|keju|mentega|"
                    "牛奶|鮮奶|鲜奶|蛋|雞蛋|鸡蛋|起司|奶油|優格|酸奶",
    "Pantry": "rice|noodle|pasta|flour|sugar|salt|oil|soy sauce|sauce|vinegar|bread|cereal|oats|"
              "coffee|tea|beras|mie|tepung|gula|garam|minyak|kecap|saus|roti|kopi|teh|"
              "米|麵|面|麵粉|面粉|糖|鹽|盐|油|醬油|酱油|醋|麵包|面包|咖啡|茶",
    "Household": "soap|dish soap|detergent|shampoo|toothpaste|tissue|toilet paper|paper towel|"
                 "trash bag|sponge|bleach|battery|batteries|"
                 "sabun|deterjen|sampo|odol|pasta gigi|tisu|"
                 "衛生紙|卫生纸|洗碗精|洗衣精|洗髮精|洗发水|牙膏|肥皂|垃圾袋|面紙|面纸|電池|电池",
}
_CATEGORY_RES = {cat: _kw_pattern(words.split("|"), plural=True)
                 for cat, words in CATEGORY_WORDS.items()}


def best_match(text: str, patterns: dict[str, re.Pattern], default: str) -> str:
    """The key whose pattern has the longest match ("telur ayam" -> eggs, not meat)."""
    best, best_len = default, 0
    for key, pattern in patterns.items():
        for m in pattern.finditer(text):
            if len(m.group(0)) > best_len:
                best, best_len = key, len(m.group(0))
    return best


def _number(s: str) -> float | int:
    f = float(s)
    return int(f) if f.is_integer() else f


def split_items(text: str) -> list[str]:
    return [p.strip() for p in _ITEM_SPLIT.split(text) if p and p.strip()]


def parse_item(text: str) -> Item:
    text = text.strip()
    qty, unit, name = None, "", text
    if m := _LEADING.match(text):
        qty, unit, name = _number(m.group(1)), m.group(2) or "", m.group(3)
    elif m := _TRAILING.match(text):
        name, qty, unit = m.group(1), _number(m.group(2)), m.group(3) or ""
    name = name.strip()
    name = name[:1].upper() + name[1:]
    return Item(name, qty, unit, best_match(name, _CATEGORY_RES, "Other"))


def parse_items(text: str) -> list[Item]:
    return [parse_item(p) for p in split_items(text)]


_PRICE = re.compile(
    r"^(?P<name>.*?)\s*(?:@|=)?\s*(?:nt\$|\$|rp\.?\s*|¥)?\s*(?P<num>\d+(?:\.\d+)?)\s*"
    r"(?P<suffix>元|塊|块|dollars?|ntd|twd|rb|ribu|k|rupiah)?$", re.I)


def parse_priced(text: str) -> tuple[Item, float | None]:
    """'eggs 65', '2 milk $90', '雞蛋65元', 'telur 25rb' -> (item, price).

    A number at the end is the price unless it carries a unit ('rice 2kg' has no price).
    """
    text = text.strip()
    m = _PRICE.match(text)
    name = m.group("name").strip() if m else ""
    # "milk x2" is a quantity, not a price.
    if not m or not name or re.search(r"[x×*]$", name, re.I):
        return parse_item(text), None
    price = float(m.group("num"))
    if (m.group("suffix") or "").lower() in ("rb", "ribu", "k"):
        price *= 1000
    return parse_item(m.group("name")), _number(str(price))


# --- dates and meals ----------------------------------------------------------

_RELATIVE = {
    "today": 0, "tonight": 0, "hari ini": 0, "nanti malam": 0, "今天": 0, "今晚": 0,
    "tomorrow": 1, "besok": 1, "明天": 1, "明晚": 1,
    "day after tomorrow": 2, "lusa": 2, "後天": 2, "后天": 2,
}
_WEEKDAYS: dict[str, int] = {}
for _i, _names in enumerate([
    "monday|mon|senin", "tuesday|tue|tues|selasa", "wednesday|wed|rabu",
    "thursday|thu|thur|thurs|kamis", "friday|fri|jumat|jum'at", "saturday|sat|sabtu",
    "sunday|sun|minggu",
]):
    for _n in _names.split("|"):
        _WEEKDAYS[_n] = _i
for _p in ("週", "周", "星期", "禮拜", "礼拜"):
    for _i, _ch in enumerate("一二三四五六日"):
        _WEEKDAYS[_p + _ch] = _i
    _WEEKDAYS[_p + "天"] = 6

_DATE_WORDS = {**{k: ("rel", v) for k, v in _RELATIVE.items()},
               **{k: ("wd", v) for k, v in _WEEKDAYS.items()}}
_DATE_RE = _kw_pattern(_DATE_WORDS)
_MD_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?![\d/])")

MEAL_WORDS = {
    "Breakfast": "breakfast|sarapan|早餐|早飯|早饭",
    "Lunch": "lunch|makan siang|午餐|中餐|午飯|午饭",
    "Dinner": "dinner|supper|makan malam|晚餐|晚飯|晚饭",
}
_MEAL_RES = {meal: _kw_pattern(words.split("|")) for meal, words in MEAL_WORDS.items()}
_FILLER = re.compile(r"(?<![a-z])(?:for|on|at|next|this|untuk|buat|pada|hari)(?![a-z])", re.I)
_INGREDIENTS_SPLIT = re.compile(r"\s+with\s+|\s+pakai\s+|\s+dengan\s+|\s+bahan\s*[:：]?\s*|"
                                r"材料[:：]?|用|[:：]", re.I)


def _remove(text: str, m: re.Match) -> str:
    return text[:m.start()] + " " + text[m.end():]


def extract_date(text: str, today: date) -> tuple[date | None, str]:
    """Find a date word and return (date, text without it)."""
    if m := _MD_RE.search(text):
        month, day = int(m.group(1)), int(m.group(2))
        try:
            d = date(today.year, month, day)
            if d < today:
                d = date(today.year + 1, month, day)
            return d, _remove(text, m)
        except ValueError:
            pass
    if m := _DATE_RE.search(text):
        how, value = _DATE_WORDS[m.group(0).lower()]
        if how == "rel":
            d = today + timedelta(days=value)
        else:
            d = today + timedelta(days=(value - today.weekday()) % 7)
        return d, _remove(text, m)
    return None, text


def extract_meal(text: str) -> tuple[str | None, str]:
    for meal, pattern in _MEAL_RES.items():
        if m := pattern.search(text):
            return meal, _remove(text, m)
    return None, text


def _tidy(text: str) -> str:
    text = _FILLER.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" ,，、")


@dataclass(frozen=True)
class MealPlan:
    dish: str
    date: date
    meal: str | None = None
    ingredients: list[str] = field(default_factory=list)


def parse_meal(text: str, today: date) -> MealPlan:
    when, text = extract_date(text, today)
    meal, text = extract_meal(text)
    parts = _INGREDIENTS_SPLIT.split(text, maxsplit=1)
    dish = _tidy(parts[0])
    ingredients = split_items(_tidy(parts[1])) if len(parts) > 1 else []
    dish = dish[:1].upper() + dish[1:]
    return MealPlan(dish, when or today, meal, ingredients)


# --- diary --------------------------------------------------------------------

MOOD_WORDS = {
    "good": "happy|glad|grateful|thankful|blessed|great|good|fun|excited|joy|joyful|love|"
            "peaceful|wonderful|amazing|"
            "senang|bahagia|syukur|bersyukur|seru|gembira|"
            "開心|开心|快樂|快乐|感恩|高興|高兴|幸福|喜樂|喜乐|平安|好棒",
    "low": "sad|tired|angry|upset|stressed|stress|anxious|lonely|sick|bad|frustrated|exhausted|"
           "worried|"
           "sedih|capek|capai|lelah|marah|kesal|stres|cemas|sakit|kecewa|"
           "難過|难过|累|生氣|生气|焦慮|焦虑|壓力|压力|傷心|伤心|失望|煩|烦",
}
_MOOD_RES = {k: _kw_pattern(v.split("|")) for k, v in MOOD_WORDS.items()}
MOODS = {"good": "😊 Good", "okay": "😐 Okay", "low": "😞 Low"}

TAG_WORDS = {
    "research": "research|experiment|hypothesis|paper|journal|data|analysis|"
                "riset|penelitian|eksperimen|研究|實驗|实验|論文|论文|數據|数据",
    "business": "business|startup|sell|market|customer|profit|price|"
                "bisnis|usaha|jual|pasar|pelanggan|untung|harga|"
                "生意|創業|创业|賣|卖|市場|市场|客戶|客户|賺錢|赚钱",
    "tech": "app|bot|code|website|ai|software|automation|"
            "aplikasi|kode|situs|otomatis|程式|程序|網站|网站|自動化|自动化",
    "family": "mom|dad|mother|father|wife|husband|son|daughter|kid|kids|family|brother|sister|"
              "grandma|grandpa|mama|papa|ibu|ayah|bapak|keluarga|anak|istri|suami|kakak|adik|"
              "nenek|kakek|媽|妈|爸|家人|老婆|老公|孩子|兒子|儿子|女兒|女儿|哥哥|姐姐|弟弟|妹妹",
    "church": "church|worship|prayer|pray|bible|sermon|fellowship|cell group|"
              "gereja|ibadah|doa|alkitab|persekutuan|"
              "教會|教会|禮拜|礼拜|禱告|祷告|聖經|圣经|小組|小组|敬拜|團契|团契",
    "work": "work|office|boss|meeting|colleague|project|deadline|job|"
            "kerja|kantor|bos|rapat|proyek|工作|公司|老闆|老板|開會|开会|同事|上班",
    "farm": "farm|durian|papaya|mango|harvest|soil|fertilizer|"
            "kebun|ladang|tanam|panen|pupuk|農場|农场|果園|果园|種植|种植|收成|肥料",
    "study": "study|class|exam|thesis|lab|lecture|professor|phd|homework|"
             "belajar|kuliah|ujian|kelas|skripsi|tesis|"
             "讀書|读书|上課|上课|考試|考试|論文|论文|實驗|实验|教授|作業|作业",
    "health": "gym|run|running|exercise|doctor|hospital|sleep|workout|"
              "olahraga|lari|dokter|rumah sakit|tidur|"
              "運動|运动|跑步|醫生|医生|生病|醫院|医院|睡覺|睡觉|健身",
}
_TAG_RES = {k: _kw_pattern(v.split("|"), plural=True) for k, v in TAG_WORDS.items()}
_PEOPLE_RE = re.compile(r"@([^\s@,，。.!?！？]+)")
_HASHTAG_RE = re.compile(r"#([^\s#,，。.!?！？]+)")


def detect_tags(text: str) -> list[str]:
    """Tags from the word lists plus any #hashtags you typed."""
    tags = [tag for tag, pattern in _TAG_RES.items() if pattern.search(text)]
    tags += [t.lower() for t in _HASHTAG_RE.findall(text)]
    return list(dict.fromkeys(tags))


@dataclass(frozen=True)
class DiaryEntry:
    title: str
    text: str
    kind: str = "Diary"
    mood: str | None = None
    tags: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)


def make_title(text: str, limit: int = 50) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    first = re.split(r"(?<=[.!?。！？])\s*", first, maxsplit=1)[0].strip()
    return first if len(first) <= limit else first[:limit].rstrip() + "…"


def mood_counts(text: str) -> tuple[int, int]:
    """(good words, low words) found in the text."""
    return len(_MOOD_RES["good"].findall(text)), len(_MOOD_RES["low"].findall(text))


def mood_key(good: int, low: int) -> str | None:
    if not good and not low:
        return None
    return "good" if good > low else "low" if low > good else "okay"


def detect_mood(text: str) -> str | None:
    key = mood_key(*mood_counts(text))
    return MOODS[key] if key else None


MOOD_TITLES = {"good": "A good day", "low": "A heavy day", "okay": "A mixed day", None: "Today"}
TAG_PHRASES = {"family": "with family", "church": "at church", "work": "at work",
               "farm": "on the farm", "study": "of study", "health": "for my health",
               "research": "in the lab", "business": "on business", "tech": "building things"}


def suggest_titles(text: str, day: date, mood: str | None = None,
                   tags: list[str] | tuple = (), prefix: str = "") -> list[str]:
    """Three title ideas: your first sentence, a mood/topic phrase, and the date."""
    first = make_title(_HASHTAG_RE.sub("", text).strip(), 40)
    first = first[:1].upper() + first[1:]
    phrase = MOOD_TITLES.get(mood, "Today")
    if prefix:
        phrase = f"{prefix}: {phrase.lower()}"
    topic = next((TAG_PHRASES[t] for t in tags if t in TAG_PHRASES), "")
    if topic:
        phrase += f" {topic}"
    dated = f"{day:%A}, {day.day} {day:%b %Y}"
    if prefix:
        dated = f"{prefix} · {dated}"
    return list(dict.fromkeys(t for t in (first, phrase, dated) if t))


def parse_diary(text: str, kind: str = "Diary") -> DiaryEntry:
    people = list(dict.fromkeys(_PEOPLE_RE.findall(text)))
    return DiaryEntry(make_title(text) or kind, text, kind, detect_mood(text),
                      detect_tags(text), people)


@dataclass(frozen=True)
class IdeaEntry:
    title: str
    text: str
    tags: list[str] = field(default_factory=list)


def parse_idea(text: str) -> IdeaEntry:
    title = make_title(_HASHTAG_RE.sub("", text).strip()) or "Idea"
    title = title[:1].upper() + title[1:]
    return IdeaEntry(title, text, detect_tags(text))
