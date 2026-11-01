# LINE Diary Bot

Send text to a LINE bot and it files it in Notion: **diary**, **ideas**,
**shopping list** or **cooking plan**. It uses keywords (English, Indonesian, 中文) and no AI, runs on your own
computer, and answers only you.

| You send | Saved to |
|---|---|
| `Helped mom in the garden, tired but happy` | 📔 Today's diary page. The first entry creates it and offers 3 titles; later entries are added under a time heading. Anything without a keyword goes here |
| `new` · `title A quiet Sunday` | Next message starts a new page for today / rename the page |
| `sos` · `reflect` · `refleksi` · `反思` | 🕊 Guided reflection: 6 questions one at a time, each answer under its heading on a Reflection page. `skip` / `done` any time |
| `share God was faithful today` · `grateful for …` | 📔 Diary as Sharing / Gratitude |
| `idea sell durian chips #farm` · `ide …` · `想法 …` · `💡 …` | 💡 Ideas (status 💡 New, tags auto + #hashtags) |
| `ideas` | Your open ideas (New and Exploring), newest first |
| `buy eggs 10, 2 milk, dish soap` · `beli telur` · `買雞蛋、牛奶` | 🛒 Shopping list, with quantity and category |
| `list` · `bought eggs 65, milk 90` · `bought all` | Shows the list / ticks items off. A number at the end is the price, saved with today's date |
| `price eggs` · `harga telur` · `價格 雞蛋` | 💰 Price history: every recorded price (newest first), lowest / highest / average |
| `price eggs 72` | Saves a price you saw, and shows what you paid last time |
| `save list weekly: eggs 10, milk, rice 2kg` | 📝 Saves a reusable list (send again to update it) |
| `use list weekly` · `lists` | Adds a saved list to the shopping list (skips items already on it) / shows saved lists as buttons |
| `cook fried rice tue dinner with rice, eggs` · `masak nasi goreng besok` · `煮番茄炒蛋 明天晚餐` | 🍳 Cooking plan. A button adds the ingredients to the shopping list |
| `menu` · `cooked fried rice` · `skip pasta` | Next 7 days / mark a meal |
| `undo` · `help` | Remove the last thing saved / examples |

If plain text was really an idea, a shopping list or a meal, tap **💡 Idea instead**,
**🛒 Shopping instead** or **🍳 Cooking instead** under the reply. `@Name` in a diary
entry fills *People*; `#word` adds a tag to a diary entry or idea.

## Notion layout

The private page **LINE Diary Bot** in your Notion holds five databases:

- **Diary**: one page per day (or more with `new`) plus Reflection pages. Title, Date,
  Kind (Diary / Sharing / Gratitude / Reflection), Mood, Tags, People. The page itself
  holds your entries under time headings, so open a row to read the day.
- **Ideas**: Idea, Date, Tags, Status (💡 New → 🔍 Exploring → ✅ Done / 🗄 Dropped).
  Your full text is the page body; change the status in Notion as an idea moves along.
- **Shopping list**: Item, Qty, Unit, Category, Bought, Bought on, Price, For meal (→ Cooking plan).
  Bought rows with a price are your price history.
- **Saved lists**: List, Items (comma-separated). Edit them in Notion or by LINE.
- **Cooking plan**: Dish, Date, Meal, Ingredients, Status, Recipe, Notes, Shopping items

You can rename options or add columns in Notion. Keep the property names above,
because the bot writes to them by name.

## Setup (about 15 minutes, once)

1. **LINE bot**: in the [LINE Developers Console](https://developers.line.biz/console/),
   create a new **Messaging API** channel (separate from Bob-bot). In the
   [Official Account Manager](https://manager.line.biz/), turn auto-reply off and webhooks
   on. Add the bot as a friend with the QR code.
2. **Notion integration**: at [notion.so/profile/integrations](https://www.notion.so/profile/integrations),
   go to *New integration* → Internal, then copy the secret. Open the **LINE Diary Bot**
   page → **⋯ → Connections** → add the integration.
3. Run:
   ```bash
   cd ~/line-diary-bot
   python3 run.py --notion   # asks for the Notion secret, checks all 5 databases
   python3 run.py            # asks for the LINE secret/token, then starts everything
   ```
   Send the bot a message: it replies with your user ID. Run `python3 run.py --setup`,
   paste the ID, then run `python3 run.py` again.

`run.py` starts the bot on port **8001** (Bob-bot uses 8000, so both can run at once),
opens a Cloudflare quick tunnel and registers the webhook with LINE automatically.
The bot only works while the window is open and the computer is awake.

## Running both bots

`bots` (in `~/.local/bin`) starts Bob-bot and this bot together in one window, each line
labelled `[bob]` or `[diary]`. Ctrl+C stops both. Setup questions only work when a bot
runs alone (`cd` into it and `python3 run.py`).

## Adding words

All keywords live in `app/parse.py`: triggers (`_add(...)`), shopping categories
(`CATEGORY_WORDS`), mood (`MOOD_WORDS`), diary tags (`TAG_WORDS`) and meals/dates.
Add a word there, then run the tests:

```bash
.venv/bin/pytest -q
```

## Layout

| File | What it does |
|---|---|
| `app/parse.py` | Keyword classifier and parsers (items, meals, dates, mood, tags) |
| `app/bot.py` | One method per action; builds the LINE replies and quick-reply buttons |
| `app/notion.py` | Small Notion API client (API version 2025-09-03, data source ids) |
| `app/main.py` | FastAPI webhook: signature check, owner-only, duplicate guard |
| `app/db.py` | Local SQLite: seen webhook ids and the last action (for undo/move) |
| `run.py` | Setup prompts, venv, uvicorn + Cloudflare tunnel, webhook registration |
