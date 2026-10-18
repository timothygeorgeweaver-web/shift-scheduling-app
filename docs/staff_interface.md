# Team interface

Administrators and schedulers see Team beneath their account details.
Employee-only accounts do not see these controls; APIs enforce permissions
independently.

Choose a location, add employees or job roles, and expand a record to edit
its name or active status. Inactive records remain visible for reactivation.
Employee profiles created here do not automatically create sign-in accounts.

Expand an employee to manage qualifications. Each qualification checkbox
saves immediately, independently of the Save details button. Successful
writes refresh the list and collapse editors. Save name edits before changing
a qualification, selecting another location, or refreshing.

Every mutation sends the CSRF token and the expected revision where required.
Conflicting edits require a refresh before retrying. Connection interruptions
ask the user to refresh and inspect the actual state before repeating a write.
A confirmed save followed by a failed refresh is reported separately.

Recent changes displays the latest twenty audit events for the location.
The backend retains the complete audit records, including actor and before/
after data. Names are rendered as text rather than HTML.

Sign-out clears Team data. Responses from a previous signed-in UI generation
cannot populate the current screen.

Browser verification is required for create/edit, qualification changes,
reactivation, refresh persistence, narrow-screen layout, and sign-out.
The automated route tests do not execute JavaScript.
