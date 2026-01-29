# bus-departure-board
An e-ink ESP32 based departure board for Portland Metro bus stops

# Arduino/ESP32 Libraries
GxEPD2 - E-Ink display driver

# Python Script Installation
install requirements
playwright install

# Open questions:
- Do we have a way to know if the data on the server is up to date?
- How do we know if it's stale? maybe could read the header or other file info
- Can we use the raw GTFS data to make a display. nix the need for a server/extra step