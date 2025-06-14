import os
import time
import json
import shutil
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

REMOTE_FOLDER_NAME = "RemoteAccessFolder"
ACCESS_REQUESTS_FOLDER = "AccessRequests"
LOG_FILE = "remote_access_log.txt"
CREDENTIALS_FILE = "credentials.json"
UPLOAD_TRACKER_FILE = "uploaded_files.json"

def authenticate_drive():
    gauth = GoogleAuth()
    gauth.LoadCredentialsFile(CREDENTIALS_FILE)
    if gauth.credentials is None:
        gauth.LocalWebserverAuth()
    elif gauth.access_token_expired:
        gauth.Refresh()
    else:
        gauth.Authorize()
    gauth.SaveCredentialsFile(CREDENTIALS_FILE)
    return GoogleDrive(gauth)

def log_activity(message, status="INFO"):
    with open(LOG_FILE, 'a') as log:
        log.write(f"{time.strftime("%d/%b/%Y:%H:%M:%S %z")} {status}: {message}\n")

def load_uploaded_files():
    if os.path.exists(UPLOAD_TRACKER_FILE):
        with open(UPLOAD_TRACKER_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_uploaded_files(data):
    with open(UPLOAD_TRACKER_FILE, 'w') as f:
        json.dump(data, f)

class RemoteAccessHandler(FileSystemEventHandler):
    def __init__(self, drive, folder_id, upload_tracker):
        self.drive = drive
        self.folder_id = folder_id
        self.upload_tracker = upload_tracker

    def on_modified(self, event):
        if not event.is_directory:
            sync_file_to_drive(self.drive, event.src_path, self.folder_id, self.upload_tracker)

    def on_created(self, event):
        if not event.is_directory:
            sync_file_to_drive(self.drive, event.src_path, self.folder_id, self.upload_tracker)

def create_drive_folder(drive, folder_name, parent_id=None):
    file_metadata = {
        'title': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
    }
    if parent_id:
        file_metadata['parents'] = [{'id': parent_id}]
    folder = drive.CreateFile(file_metadata)
    folder.Upload()
    return folder['id']

def find_existing_file(drive, folder_id, file_name):
    query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
    file_list = drive.ListFile({'q': query}).GetList()
    return file_list[0] if file_list else None

def sync_file_to_drive(drive, file_path, folder_id, upload_tracker):
    if not os.path.exists(file_path):
        return

    file_name = os.path.basename(file_path)
    mtime = os.path.getmtime(file_path)

    # Prevent double uploads
    if upload_tracker.get(file_name) == mtime:
        return

   
    existing = find_existing_file(drive, folder_id, file_name)
    params = {'title': file_name}
    if existing:
        params['id'] = existing['id']
    else:
        params['parents'] = [{'id': folder_id}]
      
    file_drive = drive.CreateFile(params)  
    file_drive.SetContentFile(file_path)
    file_drive.Upload()
    
    log_activity(f"Uploaded {file_name}")

    upload_tracker[file_name] = mtime
    save_uploaded_files(upload_tracker)

def download_new_files_from_drive(drive, folder_id, local_path, upload_tracker):
    file_list = drive.ListFile({'q': f"'{folder_id}' in parents and trashed=false"}).GetList()
    for file in file_list:
        dest_path = os.path.join(local_path, file['title'])

        remote_mtime = time.mktime(time.strptime(file['modifiedDate'], "%Y-%m-%dT%H:%M:%S.%fZ"))
        local_mtime = os.path.getmtime(dest_path) if os.path.exists(dest_path) else 0

        # Only download if remote file is newer
        if remote_mtime <= local_mtime:
            return

        file.GetContentFile(dest_path)
        os.utime(dest_path, (remote_mtime, remote_mtime))
        upload_tracker[file['title']] = remote_mtime
        save_uploaded_files(upload_tracker)
        log_activity(f"Downloaded/Updated {file['title']}")

def process_access_requests(drive, access_folder_id, local_path, upload_tracker):
    file_list = drive.ListFile({'q': f"'{access_folder_id}' in parents and trashed=false"}).GetList()
    for file in file_list:
        request_path = os.path.join(local_path, file['title'])
        file.GetContentFile(request_path)
        with open(request_path, 'r') as f:
            contents = f.read()
        os.remove(request_path)
        if False:  # Replace with secure code validation logic
            target_file = file['title'].replace(".request", "")
            file_path = os.path.join(local_path, target_file)
            if os.path.exists(file_path):
                sync_file_to_drive(drive, file_path, access_folder_id, upload_tracker)
                log_activity(f"Access granted and uploaded: {target_file}")
        file.Delete()

if __name__ == '__main__':
    drive = authenticate_drive()
    upload_tracker = load_uploaded_files()

    file_list = drive.ListFile({'q': "trashed=false and mimeType='application/vnd.google-apps.folder'"}).GetList()
    folder_ids = {f['title']: f['id'] for f in file_list}

    remote_folder_id = folder_ids.get(REMOTE_FOLDER_NAME) or create_drive_folder(drive, REMOTE_FOLDER_NAME)
    access_folder_id = folder_ids.get(ACCESS_REQUESTS_FOLDER) or create_drive_folder(drive, ACCESS_REQUESTS_FOLDER)

    # Watch local directory for changes
    path_to_watch = os.path.join(os.getcwd(), REMOTE_FOLDER_NAME)
    os.makedirs(path_to_watch, exist_ok=True)

    event_handler = RemoteAccessHandler(drive, remote_folder_id, upload_tracker)
    observer = Observer()
    observer.schedule(event_handler, path=path_to_watch, recursive=True)
    observer.start()

    try:
        while True:
            download_new_files_from_drive(drive, remote_folder_id, path_to_watch, upload_tracker)
            process_access_requests(drive, access_folder_id, path_to_watch, upload_tracker)
            time.sleep(10)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

