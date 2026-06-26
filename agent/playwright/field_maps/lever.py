"""Lever ATS field map."""

FIELD_MAP = {
    "First Name": "input[name='name']",  # Lever uses a single name field
    "Last Name": None,  # merged into First Name
    "Email": "input[name='email']",
    "Phone": "input[name='phone']",
    "LinkedIn": "input[name='urls[LinkedIn]']",
    "Resume": "input[type='file']",
}
