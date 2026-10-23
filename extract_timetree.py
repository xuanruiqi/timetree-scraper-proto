"""Scrape public TimeTree events into JSON or ICS."""

import asyncio
import argparse
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, TypeAlias, cast

from ics import Calendar, Event
from playwright.async_api import Response, async_playwright


JsonValue: TypeAlias = (
    bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
    | None
)
EventPayload: TypeAlias = dict[str, JsonValue]
OutputFormat: TypeAlias = Literal["json", "ics"]
DateValue: TypeAlias = date | datetime
ParsedDateValue: TypeAlias = DateValue | None

# Numeric timestamps larger than this are in milliseconds, not seconds.
MAX_TIMESTAMP_SECONDS = datetime.max.replace(tzinfo=timezone.utc).timestamp()

# Candidate field names for each event property, in order of preference,
# since the TimeTree payload's field names are not guaranteed.
START_KEYS = (
    "start_at", "starts_at", "start", "started_at", "start_time", "starts_on", "start_date"
)
END_KEYS = ("end_at", "ends_at", "end", "ended_at", "end_time", "ends_on", "end_date")
ALL_DAY_KEYS = ("all_day", "is_all_day", "allDay", "full_day")
TITLE_KEYS = ("title", "name", "summary")
DESCRIPTION_KEYS = ("description", "note", "memo", "details", "body")
LOCATION_KEYS = ("location", "place", "place.name", "venue", "venue.name")
UID_KEYS = ("id", "uuid", "uid")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments: the calendar URL, output path and optional format."""
    parser = argparse.ArgumentParser(
        description="Scrape public TimeTree events into JSON or ICS."
    )
    parser.add_argument("url", help="Public TimeTree calendar URL to scrape.")
    parser.add_argument("output_file", help="Output file path.")
    parser.add_argument(
        "--format",
        choices=("json", "ics"),
        help="Output format. Defaults to the output file extension, then JSON.",
    )
    return parser.parse_args()


def output_format(
    output_file: str, requested_format: OutputFormat | None
) -> OutputFormat:
    """Decide which format to write.

    An explicitly requested format wins. Otherwise a ``.ics`` extension
    (case-insensitive) selects ICS, and anything else falls back to JSON.
    """
    if requested_format:
        return requested_format
    if output_file.lower().endswith(".ics"):
        return "ics"
    return "json"


def first_value(mapping: EventPayload, *keys: str) -> JsonValue:
    """Return the value of the first key in ``keys`` that is present and non-empty.

    Keys may be dotted paths (e.g. ``"place.name"``) to look inside nested
    dicts. A key counts as missing if any part of its path is absent or its
    value is ``None`` or ``""``. Returns ``None`` if no key matches.

    This lets callers list several possible field names, since the
    TimeTree payload's field names are not guaranteed.
    """
    for key in keys:
        value = mapping
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        if value not in (None, ""):
            return value
    return None


def parse_datetime_value(value: JsonValue) -> ParsedDateValue:
    """Convert an event's start or end value to a ``datetime`` or ``date``.

    ISO 8601 strings containing ``T`` become a ``datetime``, and other strings
    are parsed as a ``date``. A trailing ``Z`` is treated as UTC. Numbers are
    Unix timestamps in seconds or milliseconds and become a UTC ``datetime``.
    Returns ``None`` for empty values, values that fail to parse, and any
    other type.
    """
    try:
        match value:
            case bool():  # Checked before int, since bool is a subclass of int.
                return None
            case str() if "T" in value:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            case str() if value:
                return date.fromisoformat(value)
            case int() | float() if value:
                timestamp = value / 1000 if value > MAX_TIMESTAMP_SECONDS else value
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            case _:
                return None
    except (OverflowError, OSError, ValueError):
        return None


def parse_bool_value(value: JsonValue) -> bool:
    """Interpret a JSON value as a boolean flag.

    Strings are true only if they are ``"1"``, ``"true"``, ``"yes"`` or
    ``"y"`` (case-insensitive, surrounding whitespace ignored). Other values
    use Python truthiness.
    """
    match value:
        case bool():
            return value
        case str():
            return value.strip().lower() in ("1", "true", "yes", "y")
        case _:
            return bool(value)


def date_part(value: DateValue) -> date:
    """Return the calendar date of a ``datetime``, or a ``date`` unchanged."""
    if isinstance(value, datetime):
        return value.date()
    return value


def as_datetime(value: DateValue) -> datetime:
    """Return a ``datetime`` unchanged, or a ``date`` as midnight on that day."""
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time())


def write_json(output_file: str, events: list[EventPayload]) -> None:
    """Write the raw events to ``output_file`` as ``{"public_events": [...]}``."""
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"public_events": events}, f, indent=2)


def set_event_times(
    event: Event, start: DateValue, end: ParsedDateValue, all_day: bool
) -> None:
    """Set the begin and end of ``event``.

    An all-day event spans whole days up to its end date when that is later
    than the start date. Otherwise it lasts a single day. A timed event with
    no end is given a one-hour duration.
    """
    if all_day or not isinstance(start, datetime):
        event.begin = as_datetime(date_part(start))
        # Must come before setting the duration, which it would otherwise extend.
        event.make_all_day()
        if end and date_part(end) > date_part(start):
            event.duration = date_part(end) - date_part(start)
    else:
        event.begin = start
        event.end = as_datetime(end) if end else start + timedelta(hours=1)


def to_ics_event(source_event: EventPayload) -> Event | None:
    """Convert a TimeTree event to an iCalendar event.

    Each field is read from the first matching name among several
    candidates (see ``first_value``). Returns ``None`` if the event has no
    parseable start time.

    - An event is all-day if it has an all-day flag or a date-only start.
    - The title defaults to ``"Untitled event"``. Description and location
      are set only when present.
    - The UID is ``timetree-<id>``, so re-imports update existing entries
      instead of duplicating them.
    """
    start = parse_datetime_value(first_value(source_event, *START_KEYS))
    if not start:
        return None

    location = first_value(source_event, *LOCATION_KEYS)
    if isinstance(location, dict):
        location = first_value(location, "name", "title", "address")
    description = first_value(source_event, *DESCRIPTION_KEYS)
    uid = first_value(source_event, *UID_KEYS)

    event = Event(name=str(first_value(source_event, *TITLE_KEYS) or "Untitled event"))
    event.description = str(description) if description else None
    event.location = str(location) if location else None
    if uid:
        event.uid = f"timetree-{uid}"
    set_event_times(
        event,
        start,
        end=parse_datetime_value(first_value(source_event, *END_KEYS)),
        all_day=parse_bool_value(first_value(source_event, *ALL_DAY_KEYS)),
    )
    return event


def write_ics(output_file: str, events: list[EventPayload]) -> None:
    """Convert the events to iCalendar entries and write them to ``output_file``.

    Events with no parseable start time are skipped, and a message is printed.
    """
    calendar = Calendar()
    for source_event in events:
        event = to_ics_event(source_event)
        if event is None:
            print(f"Skipping event with no start time: {source_event.get('id', '<unknown>')}")
        else:
            calendar.events.add(event)

    with open(output_file, "w", encoding="utf-8") as f:
        f.writelines(calendar.serialize_iter())


def write_events(
    output_file: str, events: list[EventPayload], file_format: OutputFormat
) -> None:
    """Write ``events`` to ``output_file`` in the given format."""
    if file_format == "ics":
        write_ics(output_file, events)
    else:
        write_json(output_file, events)


async def main() -> None:
    """Scrape a public TimeTree calendar and write its events to a file.

    Opens the calendar URL in headless Chromium and collects events from the
    ``public_events`` API responses the page makes. It then clicks "Next
    month" until one of these happens:

    - a month returns no events
    - the button disappears
    - no response arrives within 5 seconds

    Scraping starts at the month the page opens on, so earlier months are not
    collected.
    """
    args = parse_args()
    requested_format = cast(OutputFormat | None, args.format)
    file_format = output_format(args.output_file, requested_format)

    all_events: list[EventPayload] = []
    current_request_done = asyncio.Event()
    last_response_empty = False

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        async def handle_response(response: Response) -> None:
            """Collect events from ``public_events`` GET responses.

            Records whether a response had no events, which ends the scrape.
            Also signals ``current_request_done`` so the main loop can move to
            the next month. The signal is sent even if reading the response
            fails.
            """
            nonlocal last_response_empty
            if "public_events" in response.url and response.request.method == "GET":
                try:
                    data: Any = await response.json()
                    events = data.get("public_events", [])
                    if not isinstance(events, list):
                        events = []
                    print(f"Captured {len(events)} events from {response.url}")
                    if not events:
                        last_response_empty = True
                    else:
                        all_events.extend(
                            cast(EventPayload, event)
                            for event in events
                            if isinstance(event, dict)
                        )
                    current_request_done.set()
                except Exception as e:  # pylint: disable=broad-exception-caught
                    print(e)
                    current_request_done.set()

        page.on("response", handle_response)

        # Initial load
        current_request_done.clear()
        await page.goto(args.url, wait_until="networkidle")

        # Wait for the API request to finish
        try:
            await asyncio.wait_for(current_request_done.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            pass

        while not last_response_empty:
            # Click next month button
            next_btn = page.locator('button[aria-label="Next month"]')
            if await next_btn.count() == 0:
                print("No next button found, stopping.")
                break

            print("Clicking Next month...")
            current_request_done.clear()
            await next_btn.click()

            # Wait for API response
            try:
                await asyncio.wait_for(current_request_done.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                print("Timeout waiting for events response, assuming end.")
                break

            await asyncio.sleep(1) # Small pause to let DOM update if needed

        write_events(args.output_file, all_events, file_format)

        print(f"Total events scraped: {len(all_events)}")
        print(f"Wrote {file_format.upper()} output to {args.output_file}")
        await browser.close()


def cli() -> None:
    """Entry point for the ``extract-timetree`` console script."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
