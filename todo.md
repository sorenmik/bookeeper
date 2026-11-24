# Bookeeper

I need a book library management software to keep track of my books in my home library.

It should be a webpage made in Python.


## Features

- Add/edit/remove/update books from library
    - Add books by scanning ISBN barcodes live with the camera on iPhone or Android. Serve the app over HTTPS with a certificate the phone trusts, and allow typing or pasting the ISBN as a fallback.
    - Add books manually when they have no ISBN or the ISBN lookup fails.
    - Warn when the book is already in the library, but allow adding another copy as a separate inventory item.
- Look up book details and summaries by ISBN using Open Library. Enter missing details manually and show the location of any owned copies.
- Search page for finding books by title, author, series, tag, or ISBN. Filter by room, shelf, reading status, or loan status.
- Each household account has its own private wishlist. When a member marks a book as bought, automatically move it from their wishlist into the shared inventory.
- Side-view shelf map: storages contain cabinets (n×m), shelves, boxes and piles, with books assigned to slots. Done: path display, search, filters, unplaced list, bulk move with undo.
    - Later: pick move targets on the map, zoom/pan, quick assign, labels/QR.
    - Posibillity to add personal notes to books
- Page where the software can recommend how to sort the books by genre, type, author etc
- Shared household reading history with statuses (unread, reading, finished, abandoned) and a finish date for each read, including rereads.
- Stats page for the shared library, including how many books the household finished this year.
- Page showing books I have borrowed from others or lent out etc
- Make a small QR code on the page, so I can scan it with a phone and go the the page (in case the page runs at an obscure IP addr)
- Backup page, where I can make snapshots of database and download database for backup and migration
    - Restore the library from a backup snapshot.
    - Export the catalog as a CSV file that opens in spreadsheet apps.


## Testing

- Seed a separate test library with 200 sample books using `flask --app bookeeper seed-demo`.

## Deployment and backup decisions

- Serve HTTPS through a trusted reverse proxy on the home network. The local launcher stays loopback-only.
- The first household account can create, download, and restore shared snapshots. Exclude accounts, private wishlists, and personal notes.


## Graphical

- Themes:
    - Simple: near-black surfaces with orange highlights (default).
    - Burnley, 1657: an old English monastery library. Monks reading while drinking an ale, scrolls, parchment, and candlelight.
    - Future: sci-fi. Foreign worlds, planets, spaceships, and nebulas behind semi-opaque panels, with sci-fi humour.
    - Summer evening: reading a book under an apple tree on a sunny summer evening. Fireflies, the sun going down, the moon rising, cozy.
    - Pirate tavern: classic adventure-game style pixel art. Night, blue and orange, wood and fireplaces.
- Flatpak/flathub layout looking


## Software stack

- Store library data in a local JSON file. Serialize read-modify-write operations and replace the file atomically to avoid lost or partial updates when multiple household members edit it.
- Build the website in Python using Flask and Jinja templates.
- Runs in browser as webpage
    - For PC and mobile devices
    - Initially accessible only on the home network, with no public access. Keep the data location configurable so the app can move to a server or cloud host later.
    - Give household members separate sign-ins to the same shared library.
- Use a python .venv to run in
- Start a git-project locally to help track changes

