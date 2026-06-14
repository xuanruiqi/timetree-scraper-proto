import asyncio
import argparse
import json
from datetime import date, datetime, timedelta
from playwright.async_api import async_playwright


def parse_args():
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


def output_format(output_file, requested_format):
    if requested_format:
        return requested_format
    if output_file.lower().endswith(".ics"):
        return "ics"
    return "json"


def first_value(mapping, *keys):
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


def parse_datetime_value(value):
    if not value:
        return None
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            if "T" in normalized:
                return datetime.fromisoformat(normalized)
            return date.fromisoformat(normalized)
        except ValueError:
            return value
    return value


def parse_bool_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


def date_part(value):
    if isinstance(value, datetime):
        return value.date()
    return value


def write_json(output_file, events):
    with open(output_file, "w") as f:
        json.dump({"public_events": events}, f, indent=2)


def write_ics(output_file, events):
    from ics import Calendar, Event

    calendar = Calendar()

    for source_event in events:
        start = parse_datetime_value(
            first_value(
                source_event,
                "start_at",
                "starts_at",
                "start",
                "started_at",
                "start_time",
                "starts_on",
                "start_date",
            )
        )
        if not start:
            print(f"Skipping event with no start time: {source_event.get('id', '<unknown>')}")
            continue

        end = parse_datetime_value(
            first_value(
                source_event,
                "end_at",
                "ends_at",
                "end",
                "ended_at",
                "end_time",
                "ends_on",
                "end_date",
            )
        )
        all_day = parse_bool_value(
            first_value(source_event, "all_day", "is_all_day", "allDay", "full_day")
        ) or (isinstance(start, date) and not isinstance(start, datetime))

        event = Event()
        event.name = str(
            first_value(source_event, "title", "name", "summary") or "Untitled event"
        )

        if all_day:
            event.begin = date_part(start)
            event.make_all_day()
            if end:
                start_date = date_part(start)
                end_date = date_part(end)
                if isinstance(start_date, date) and isinstance(end_date, date):
                    duration = end_date - start_date
                else:
                    duration = None
                if duration and duration.days > 0:
                    event.duration = duration
        else:
            event.begin = start
            if end:
                event.end = end
            elif isinstance(start, datetime):
                event.end = start + timedelta(hours=1)
            elif isinstance(start, date):
                event.end = start + timedelta(days=1)

        description = first_value(
            source_event,
            "description",
            "note",
            "memo",
            "details",
            "body",
        )
        if description:
            event.description = str(description)

        location = first_value(
            source_event,
            "location",
            "place",
            "place.name",
            "venue",
            "venue.name",
        )
        if isinstance(location, dict):
            location = first_value(location, "name", "title", "address")
        if location:
            event.location = str(location)

        uid = first_value(source_event, "id", "uuid", "uid")
        if uid:
            event.uid = f"timetree-{uid}"

        calendar.events.add(event)

    with open(output_file, "w") as f:
        f.writelines(calendar.serialize_iter())


def write_events(output_file, events, file_format):
    if file_format == "ics":
        write_ics(output_file, events)
    else:
        write_json(output_file, events)


async def main():
    args = parse_args()
    file_format = output_format(args.output_file, args.format)
    
    all_events = []
    current_request_done = asyncio.Event()
    last_response_empty = False

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        async def handle_response(response):
            nonlocal last_response_empty
            if "public_events" in response.url and response.request.method == "GET":
                try:
                    data = await response.json()
                    events = data.get("public_events", [])
                    print(f"Captured {len(events)} events from {response.url}")
                    if not events:
                        last_response_empty = True
                    else:
                        all_events.extend(events)
                    current_request_done.set()
                except Exception as e:
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


def cli():
    asyncio.run(main())


if __name__ == "__main__":
    cli()
