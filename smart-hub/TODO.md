# TODO

- [x] Inspect existing UI routes and placeholders for "My Bookings".

- [ ] Update `app.py`:
  - [ ] Extend `init_db()` to create `bookings` table.
  - [ ] Add routes: `/bookings` (history), `/booking/create` (create), `/booking/cancel/<id>` (cancel).
  - [ ] Update `/dashboard` to link sidebar "My Bookings" to `/bookings`.
- [ ] Update `templates/dashboard.html`:
  - [ ] Replace "Book Now" mock alert with a form POST to `/booking/create`.
  - [ ] Update sidebar link for "My Bookings" to `/bookings`.
- [ ] Create `templates/bookings.html`:
  - [ ] Show booking history for logged-in user.
  - [ ] Show "Cancel Booking" button for active bookings.
- [x] Update `static/style.css` (minimal):
  - [x] Add basic styles for booking list/cards.

- [ ] Run app and test flow: create booking -> view history -> cancel -> history updates.

