# TODO

## Booking reminder notifications (15 minutes before)
- [ ] Inspect current booking creation logic in `app.py` (find where `INSERT INTO bookings` happens).
- [ ] Add DB schema for notification storage (create `booking_notifications` and optionally `user_notifications`).
- [ ] After booking is created, schedule a reminder: set `remind_at = booking_datetime - 15 minutes`.
- [ ] Add a background scheduler loop inside Flask that periodically checks due reminders and marks them sent + saves the message.
- [ ] Add a `/notifications` route and template rendering latest notifications for the logged-in user.
- [ ] Add/adjust navigation link “Notifications” in the student UI template(s) to point to `/notifications`.
- [ ] Smoke test: create a booking with start time soon; verify reminder appears.

