# --- One time use utility to migrate old flat text lore files to the new structured JSON format. ---
import os
import json
import time

def migrate_txt_to_json():
    # Get the absolute path to the core/lores directory
    base_dir = os.path.dirname(os.path.abspath(__file__))
    lores_dir = os.path.join(base_dir, "core", "lores")
    
    if not os.path.exists(lores_dir):
        print(f"Error: Could not find the directory at {lores_dir}")
        return

    # Grab the exact current Unix timestamp for the 'added_at' field
    current_time = int(time.time())
    files_migrated = 0

    print("Starting lore migration...")

    for filename in os.listdir(lores_dir):
        if filename.endswith(".txt"):
            file_path = os.path.join(lores_dir, filename)
            json_filename = filename.replace(".txt", ".json")
            json_path = os.path.join(lores_dir, json_filename)

            # Read the old flat text file
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()

            # Split by double newlines to isolate logical paragraphs
            chunks = [chunk.strip() for chunk in raw_text.split("\n\n") if chunk.strip()]
            
            # Build the new structured JSON dictionaries
            json_data = []
            for chunk in chunks:
                json_data.append({
                    "text": chunk,
                    "added_at": current_time,
                    "hits": 0,
                    "is_core": False
                })

            # Save the new JSON file
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, indent=4, ensure_ascii=False)
            
            print(f" [SUCCESS] Migrated {filename} -> {json_filename} ({len(chunks)} memories extracted)")
            files_migrated += 1

    print(f"\nMigration complete. {files_migrated} files successfully converted to JSON format.")
    print("You can safely delete the old .txt files once you verify the new .json files look correct.")

if __name__ == "__main__":
    migrate_txt_to_json()