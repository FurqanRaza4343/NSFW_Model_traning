/**
 * Receives registrations from the Image Moderation app and appends them to a "Leads" sheet.
 *
 * Setup (about 3 minutes):
 *  1. Open your Google Sheet > Extensions > Apps Script, paste this file.
 *  2. Replace PASTE_YOUR_TOKEN_HERE with the token from leads_setup.txt (the same token is stored in the
 *     Modal secret "leads-sink"). Never commit the real token.
 *  3. Deploy > New deployment > type "Web app" > Execute as: Me > Who has access: Anyone > Deploy.
 *  4. Authorise when asked, copy the Web app URL and give it to the person who runs the Modal app.
 *
 * Only the app that knows the token can add rows. Names are stored as text (formula injection is blocked).
 */
const TOKEN = 'PASTE_YOUR_TOKEN_HERE';
const SHEET_NAME = 'Leads';

function doPost(e) {
  try {
    const d = JSON.parse(e.postData.contents);
    if (d.token !== TOKEN) return reply('forbidden');
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let sh = ss.getSheetByName(SHEET_NAME);
    if (!sh) {
      sh = ss.insertSheet(SHEET_NAME);
      sh.appendRow(['Received (UTC)', 'Event', 'Name', 'Email', 'Phone', 'Consent at', 'Registered at', 'Source']);
      sh.setFrozenRows(1);
    }
    sh.appendRow([new Date(), safe(d.event), safe(d.name), safe(d.email), safe(d.phone),
                  safe(d.consent_at), safe(d.created), safe(d.source)]);
    return reply('ok');
  } catch (err) {
    return reply('error');
  }
}

// A cell that starts with = + - @ could be run as a formula: force it to text.
function safe(v) {
  v = String(v == null ? '' : v);
  return /^[=+\-@]/.test(v) ? "'" + v : v;
}

function reply(text) {
  return ContentService.createTextOutput(text);
}
