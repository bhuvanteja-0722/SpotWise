"""Transit feed discovery links, checked 2026-11-06. Links point to publishers/catalogues."""

TRANSIT_SOURCES = {
    "Ahmedabad": [
        {"name": "Ahmedabad bus GTFS · Janmarg + AMTS", "mode": "Bus", "url": "https://mobilitydatabase.org/feeds/gtfs/mdb-3131", "kind": "GTFS schedule", "freshness": "Feed snapshot checked Oct 1, 2026; service shown through Mar 29, 2027.", "provenance": "Community feed with one reported validation error; inspect the report and license before reuse."},
        {"name": "Mobility Database India feed catalogue", "mode": "Multi-city", "url": "https://mobilitydatabase.org/feeds?q=india", "kind": "Feed directory", "freshness": "Catalog checks registered feed URLs daily.", "provenance": "Check the individual feed page and service dates before downloading."},
    ],
    "Bengaluru": [
        {"name": "BMTC bus GTFS", "mode": "Bus", "url": "https://mobilitydatabase.org/feeds?q=india", "kind": "GTFS schedule", "freshness": "Use the current BMTC entry in the feed catalogue; an older archived entry is stale/replaced.", "provenance": "BMTC timetable data; the current catalogue entry and license should be checked before use."},
        {"name": "Namma Metro GTFS", "mode": "Metro", "url": "https://metroroute.net/data-sources", "kind": "GTFS schedule", "freshness": "Feed version 20260817; stated validity Aug 17, 2026–Aug 17, 2027.", "provenance": "Community transcription of BMRCL schedules; intermediate stop times are estimated."},
    ],
    "Chennai": [
        {"name": "CUMTA unified transit GTFS", "mode": "Bus · metro · suburban rail", "url": "https://opendata.cumta.org/", "kind": "GTFS schedule", "freshness": "Portal reports a unified repository; check each feed's own calendar before use.", "provenance": "CUMTA portal for MTC, Chennai Metro and suburban rail; schedules are static, not vehicle tracking."},
    ],
    "Delhi NCR": [
        {"name": "Delhi Bus Open Transit Data", "mode": "Bus", "url": "https://otd.delhi.gov.in/documentation/", "kind": "GTFS schedule + authorized real-time", "freshness": "Use the portal's current feed; static stop_times may be rough estimates.", "provenance": "Government of NCT Delhi / IIIT-Delhi. Real-time data requires authorization and a private key."},
        {"name": "Delhi Metro and station footfall feeds", "mode": "Metro", "url": "https://delhi.transportstack.in/data-services", "kind": "GTFS / hourly footfall", "freshness": "Provider catalogue; station footfall access is approval-based.", "provenance": "DMRC static data and hourly station entries/exits; station counts are not street footfall."},
    ],
    "Hyderabad": [
        {"name": "TGSRTC bus GTFS", "mode": "Bus", "url": "https://tgsrtc.telangana.gov.in/open-data", "kind": "GTFS schedule", "freshness": "Feed version 20260201; service period Feb 1, 2026–Feb 1, 2031 (catalog checked Sep 30, 2026).", "provenance": "Official TGSRTC open-data page. Free use requires attribution and compliance with its terms."},
        {"name": "Hyderabad Metro GTFS", "mode": "Metro", "url": "https://hmrl.co.in/open-data.html", "kind": "GTFS schedule", "freshness": "Official feed page; verify the downloaded feed calendar and current revision.", "provenance": "Hyderabad Metro Rail Ltd.; official open data terms apply."},
        {"name": "MMTS rail feed directory", "mode": "Suburban rail", "url": "https://mobilitydatabase.org/feeds?q=india", "kind": "GTFS schedule", "freshness": "A catalogued MMTS feed is marked expired; do not use until a current version is verified.", "provenance": "Use the operator or catalog only after confirming active service dates."},
    ],
    "Kolkata": [
        {"name": "Mobility Database India feed catalogue", "mode": "Bus · metro · rail", "url": "https://mobilitydatabase.org/feeds?q=india", "kind": "Feed directory", "freshness": "No current validated Kolkata GTFS feed was verified in this search; inspect the catalogue for new entries.", "provenance": "Use only a feed with current service dates and a clear publisher/license."},
        {"name": "Metro Railway Kolkata", "mode": "Metro", "url": "https://mtp.indianrailways.gov.in/", "kind": "Official service information", "freshness": "Operator service notices and timetable information; not a GTFS bulk feed.", "provenance": "Indian Railways operator source."},
    ],
    "Mumbai": [
        {"name": "Mumbai bus GTFS community feed", "mode": "Bus", "url": "https://github.com/croyla/mumbai-gtfs", "kind": "GTFS schedule", "freshness": "Community feed; check the latest commit and service calendars before use.", "provenance": "BEST/TMT/KDMT compilation; not a unified official local-train or metro real-time feed."},
        {"name": "Indian Railways GTFS · unofficial community", "mode": "Train", "url": "https://mobilitydatabase.org/feeds/gtfs/mdb-2867", "kind": "GTFS schedule", "freshness": "Feed snapshot checked Aug 31, 2026; no active service-date range is published, so current validity is unverified.", "provenance": "Unofficial all-India feed. SpotWise filters imported stop coordinates to the Mumbai metro-area bounds; confirm the feed calendar and operator schedule."},
        {"name": "Mobility Database India feed catalogue", "mode": "Bus · metro · rail", "url": "https://mobilitydatabase.org/feeds?q=india", "kind": "Feed directory", "freshness": "No verified unified, current official Mumbai suburban rail + metro GTFS feed found.", "provenance": "For metro, verify operator schedules separately; the included Kaggle railway table is a 2015 snapshot."},
    ],
    "Pune": [
        {"name": "PMPML bus GTFS", "mode": "Bus", "url": "https://mobilitydatabase.org/feeds/gtfs/mdb-3137", "kind": "GTFS schedule", "freshness": "Latest snapshot checked Oct 8, 2026; service period Oct 7, 2026–Apr 5, 2027.", "provenance": "Community feed with no validation errors in the latest check; inspect the report and license."},
        {"name": "Pune Metro service information", "mode": "Metro", "url": "https://www.punemetrorail.org/", "kind": "Official service information", "freshness": "Check operator service notices; no current open Pune Metro GTFS feed was verified here.", "provenance": "Maha Metro official operator site."},
    ],
}


def sources_for_city(city: str) -> list[dict]:
    return TRANSIT_SOURCES.get(city.strip(), [
        {"name": "Mobility Database feed catalogue", "mode": "Search by city and operator", "url": "https://mobilitydatabase.org/feeds?q=india", "kind": "Feed directory", "freshness": "Catalog checks registered feed URLs daily; verify the service dates on the selected feed.", "provenance": "Do not treat a listing as current until its date range, validation, publisher and license are checked."}
    ])
