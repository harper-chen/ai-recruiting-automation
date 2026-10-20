/**
 * Resume-upload proxy (Google Apps Script web app).
 *
 * Why this exists: a Google service account has no Drive storage quota of its own, so files it
 * uploads fail with a 0-byte quota error. This script runs as a normal Google user instead.
 * app.py POSTs {fileName, mimeType, fileData (base64)} and expects the file's share link back
 * as plain text. Any failure must return text that starts with "Error" (app.py checks for it).
 *
 * Setup (see DEPLOYMENT_GUIDE.md):
 *   1. script.google.com -> New project -> paste this file.
 *   2. Set FOLDER_ID below to the Drive folder that should hold the resumes.
 *   3. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone.
 *   4. Put the resulting /exec URL in your Streamlit secrets as APPS_SCRIPT_URL.
 *
 * Security note: "Anyone" means anyone who knows the URL can upload to that folder. Treat the URL
 * as a secret (keep it in secrets, never in the repo) and redeploy to rotate it if it leaks.
 */

var FOLDER_ID = "PASTE_YOUR_DRIVE_FOLDER_ID_HERE";

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var bytes = Utilities.base64Decode(data.fileData);
    var blob = Utilities.newBlob(bytes, data.mimeType || "application/pdf", data.fileName);
    var file = DriveApp.getFolderById(FOLDER_ID).createFile(blob);
    file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);

    return ContentService.createTextOutput(file.getUrl());
  } catch (err) {
    return ContentService.createTextOutput("Error: " + err);
  }
}
