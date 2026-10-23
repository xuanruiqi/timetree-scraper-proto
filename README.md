# timetree-scraper

Scrape events from a public [TimeTree](https://timetreeapp.com/) calendar and save them as JSON or as an iCalendar (`.ics`) file you can import into Google Calendar, Apple Calendar, Outlook, and others.

[TimeTree](https://timetreeapp.com/) の公開カレンダーから予定を取得し、JSON または iCalendar（`.ics`）形式で保存するツールです。ICS ファイルは Google カレンダー、Apple カレンダー、Outlook などに読み込めます。

The scraper opens the calendar in a headless Chromium browser with [Playwright](https://playwright.dev/python/), captures the `public_events` API responses the page makes, and clicks "Next month" repeatedly until a month comes back with no events.

[Playwright](https://playwright.dev/python/) でヘッドレス Chromium を起動してカレンダーを開き、ページが送信する `public_events` API のレスポンスを取得します。予定が 0 件の月が返ってくるまで「Next month」（翌月）ボタンをクリックし続けます。

## Requirements / 動作環境

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) or pip
- Chromium for Playwright / Playwright 用 Chromium

## Installation / インストール

uv:

```sh
uv sync
uv run playwright install chromium
```

pip:

```sh
python -m venv .venv
source .venv/bin/activate
pip install .
playwright install chromium
```

## Usage / 使い方

```sh
uv run extract-timetree <url> <output_file> [--format {json,ics}]

# e.g. / 例
uv run extract-timetree https://timetreeapp.com/public_calendars/example events.ics
uv run extract-timetree https://timetreeapp.com/public_calendars/example events.json
```

| Argument / 引数 | Description | 説明 |
| --- | --- | --- |
| `url` | URL of the public TimeTree calendar, e.g. `https://timetreeapp.com/public_calendars/<calendar_id>` | TimeTree 公開カレンダーの URL |
| `output_file` | Path to write the results to | 出力先のファイルパス |
| `--format` | `json` or `ics`. If omitted, `.ics` files get ICS and anything else gets JSON | `json` または `ics`。省略時は拡張子が `.ics` なら ICS、それ以外は JSON |

You can also run the script directly with `python extract_timetree.py <url> <output_file>`.

`python extract_timetree.py <url> <output_file>` のようにスクリプトを直接実行することもできます。

## Output / 出力形式

### JSON

The raw event objects returned by TimeTree, unchanged.

TimeTree が返した予定オブジェクトをそのまま保存します。

```json
{
  "public_events": [
    { "id": "...", "title": "...", "start_at": "...", "end_at": "...", ... }
  ]
}
```

### ICS

| ICS | TimeTree fields / フィールド |
| --- | --- |
| Title / タイトル | `title`, `name`, `summary` |
| Start, end / 開始・終了日時 | `start_at`, `end_at`, and similar / など |
| All-day / 終日 | `all_day`, and similar / など, or a date-only start / または日付のみの開始日 |
| Description / 説明 | `description`, `note`, `memo` |
| Location / 場所 | `location`, `place`, `venue` |
| UID | `timetree-<id>` |

Timed events with no end are given a one-hour duration. Because the UID is stable, re-importing the file updates existing entries instead of duplicating them. Events with no start time are skipped, and a message is printed for each one.

終了日時がない時刻指定の予定は 1 時間の予定として扱います。UID が固定されているため、同じファイルを再度読み込んでも予定は重複せず、既存の予定が更新されます。開始日時のない予定はスキップされ、その旨がメッセージで表示されます。

## Limitations / 制限事項

- Only **public** TimeTree calendars are supported. Private calendars need a login, which this tool does not do.
- Scraping starts from the month the page opens on (normally the current month) and moves forward only, so earlier events are not collected.
- Scraping stops at the first month with no events. Events after an empty month are missed.
- The tool depends on TimeTree's web page and internal API (the "Next month" button's `aria-label` and the `public_events` endpoint), so changes on TimeTree's side can break it.

Please follow TimeTree's terms of service and respect the calendar owner's rights when using the data you collect.

- 対応しているのは TimeTree の**公開**カレンダーのみです。非公開カレンダーにはログインが必要ですが、本ツールはログインに対応していません。
- ページを開いた時点で表示される月（通常は今月）から先の月のみを取得します。それより前の予定は取得されません。
- 予定が 0 件の月に達した時点で取得を終了します。そのため、予定のない月より後の予定は取得されません。
- TimeTree のウェブページおよび内部 API（「Next month」ボタンの `aria-label` や `public_events` エンドポイント）に依存しているため、TimeTree 側の仕様変更により動作しなくなる可能性があります。

取得したデータを利用する際は、TimeTree の利用規約を守り、カレンダー所有者の権利を尊重してください。
