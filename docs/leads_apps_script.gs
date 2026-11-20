/**
 * Receives data from the Image Moderation app (runs as YOU, inside your Google account):
 *   - "Leads" sheet : one row per registration / unlock
 *   - "Checks" sheet: one row per image check (decision, scores, SHA-256 fingerprint, link to the stored image)
 *   - Drive folder  : images of consenting people (approved / review only; the app never sends rejected images)
 *
 * Setup (about 5 minutes):
 *  1. Open your Google Sheet > Extensions > Apps Script, paste this file.
 *  2. Replace PASTE_YOUR_TOKEN_HERE with the token from leads_setup.txt (the same token is in the Modal secret
 *     "leads-sink"). Never commit the real token.
 *  3. Create a PRIVATE Drive folder (do not share it), open it, and copy the folder ID from its link
 *     (the text after /folders/). Paste it into FOLDER_ID below. Leave it empty to keep images off Drive.
 *  4. Run setupTrigger() once (Run button) and authorise: it installs the daily clean-up that deletes images
 *     older than RETENTION_DAYS.
 *  5. Deploy > Manage deployments > pencil icon > Version: New version > Deploy. The web app URL stays the same.
 *
 * Only the app that knows the token can write. Names are stored as text (formula injection is blocked).
 * Deleted files go to the Drive trash first; empty the trash to remove them for good.
 */
const TOKEN = 'PASTE_YOUR_TOKEN_HERE';
const FOLDER_ID = '';           // private Drive folder for images. Empty = images are NOT stored.
const RETENTION_DAYS = 30;
const LEADS_SHEET = 'Leads';
const CHECKS_SHEET = 'Checks';

function doPost(e) {
  try {
    const d = JSON.parse(e.postData.contents);
    if (d.token !== TOKEN) return reply('forbidden');
    switch (d.action || 'lead') {
      case 'lead':
        append(LEADS_SHEET, ['Received (UTC)', 'Event', 'Name', 'Email', 'Phone', 'Consent at', 'Registered at', 'Source'],
               [new Date(), safe(d.event), safe(d.name), safe(d.email), safe(d.phone), safe(d.consent_at), safe(d.created), safe(d.source)]);
        return reply('ok');
      case 'check':
        return reply(addCheck(d));
      case 'delete_person':
        return reply('ok:deleted:' + deletePerson(d.person));
      default:
        return reply('unknown');
    }
  } catch (err) {
    return reply('error');
  }
}

function addCheck(d) {
  let link = '';
  let note = 'ok';
  if (d.image_b64) {
    if (!FOLDER_ID) {
      note = 'ok:nostore';
    } else {
      const name = personKey(d.person) + '_' + Utilities.formatDate(new Date(), 'UTC', 'yyyyMMdd-HHmmss') + '_' +
                   String(d.decision) + '_' + String(d.sha256).slice(0, 8) + String(d.ext || '.bin');
      const blob = Utilities.newBlob(Utilities.base64Decode(d.image_b64), d.mime || 'application/octet-stream', name);
      link = DriveApp.getFolderById(FOLDER_ID).createFile(blob).getUrl();   // private: only you can open it
      note = 'ok:stored';
    }
  }
  append(CHECKS_SHEET,
         ['Received (UTC)', 'Source', 'Name', 'Email', 'Key / label', 'Decision', 'Explicit', 'Suggestive', 'Bytes', 'SHA-256', 'Image link'],
         [new Date(), safe(d.source), safe(d.name), safe(d.email), safe(d.label), safe(d.decision),
          Number(d.nsfw), Number(d.suggestive), Number(d.bytes), safe(d.sha256), link]);
  return note;
}

// Privacy request: trash every stored image of one person.
function deletePerson(person) {
  const key = personKey(person);
  if (!FOLDER_ID || key.length < 8) return 0;
  const files = DriveApp.getFolderById(FOLDER_ID).getFiles();
  let n = 0;
  while (files.hasNext()) {
    const f = files.next();
    if (f.getName().indexOf(key + '_') === 0) { f.setTrashed(true); n++; }
  }
  return n;
}

// Daily clean-up (installed by setupTrigger): trash images older than RETENTION_DAYS.
function cleanup() {
  if (!FOLDER_ID) return;
  const cutoff = Date.now() - RETENTION_DAYS * 86400000;
  const files = DriveApp.getFolderById(FOLDER_ID).getFiles();
  let n = 0;
  while (files.hasNext()) {
    const f = files.next();
    if (f.getDateCreated().getTime() < cutoff) { f.setTrashed(true); n++; }
  }
  console.log('cleanup trashed ' + n + ' file(s)');
}

function setupTrigger() {
  ScriptApp.getProjectTriggers().filter(function (t) { return t.getHandlerFunction() === 'cleanup'; })
    .forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('cleanup').timeBased().everyDays(1).atHour(3).create();
}

function append(sheetName, headers, row) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let sh = ss.getSheetByName(sheetName);
    if (!sh) {
      sh = ss.insertSheet(sheetName);
      sh.appendRow(headers);
      sh.setFrozenRows(1);
    }
    sh.appendRow(row);
  } finally {
    lock.releaseLock();
  }
}

function personKey(p) {
  return String(p || 'anon').replace(/[^a-zA-Z0-9]/g, '').slice(0, 12) || 'anon';
}

// A cell that starts with = + - @ could be run as a formula: force it to text.
function safe(v) {
  v = String(v == null ? '' : v);
  return /^[=+\-@]/.test(v) ? "'" + v : v;
}

function reply(text) {
  return ContentService.createTextOutput(text);
}
