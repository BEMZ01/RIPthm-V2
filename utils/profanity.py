import requests
import os
import json
import hashlib
import re

class ProfanityFilter:
    def __init__(self):
        self.blocklist = []
        self.blocklist_path = "temp/blocklist.json"
        # Ensure temp directory exists
        os.makedirs("temp", exist_ok=True)
        self.load_blocklist()

    def load_blocklist(self):
        if os.path.exists(self.blocklist_path):
            with open(self.blocklist_path, "r") as f:
                self.blocklist = json.load(f)
        else:
            self.download_blocklist()

    def verify_sha(self, content, expected_sha):
        """Verify the SHA-1 hash of downloaded content matches the expected value from GitHub API."""
        # GitHub uses git blob SHA-1, which includes a header
        # Format: "blob {size}\0{content}"
        # Normalize line endings to LF (what GitHub uses)
        normalized_content = content.replace('\r\n', '\n').replace('\r', '\n')

        # Encode to bytes (UTF-8 is standard for text files)
        content_bytes = normalized_content.encode('utf-8')

        # Create git blob format: "blob {size}\0{content}"
        blob_header = f"blob {len(content_bytes)}\0".encode('utf-8')
        blob_content = blob_header + content_bytes

        calculated_sha = hashlib.sha1(blob_content).hexdigest()
        return calculated_sha == expected_sha

    def download_blocklist(self):
        print("Downloading blocklist...")
        url = "https://api.github.com/repos/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/git/trees/master?recursive=1"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            verified_words = []
            failed_verifications = []

            for file in data["tree"]:
                if file["path"] not in ["LICENSE", "README.md", "USERS.md"]:
                    print(f"Downloading {file['path']}...")
                    raw_url = f"https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/{file['path']}"
                    file_response = requests.get(raw_url)

                    if file_response.status_code == 200:
                        content = file_response.text
                        expected_sha = file.get("sha")

                        if expected_sha and self.verify_sha(content, expected_sha):
                            print(f"✓ SHA verified for {file['path']}")
                            words = content.split("\n")
                            verified_words.extend(words)
                        elif expected_sha:
                            print(f"✗ SHA verification failed for {file['path']} - skipping")
                            failed_verifications.append(file['path'])
                        else:
                            print(f"⚠ No SHA available for {file['path']} - adding without verification")
                            words = content.split("\n")
                            verified_words.extend(words)
                    else:
                        print(f"Failed to download {file['path']}")

            if failed_verifications:
                print(f"Warning: {len(failed_verifications)} file(s) failed SHA verification and were skipped")

            # Remove empty strings and duplicates for better performance
            self.blocklist = list(set([word.strip() for word in verified_words if word.strip()]))

            with open(self.blocklist_path, "w") as f:
                json.dump(self.blocklist, f)

            print(f"Blocklist created with {len(self.blocklist)} entries")
        else:
            print("Failed to download blocklist.")

    def filter(self, text):
        """Replace profane words with asterisks matching their length."""
        for word in self.blocklist:
            if word:
                # Use word boundaries to match whole words only
                # This prevents "tit" from matching in "title"
                pattern = re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
                replacement = '*' * len(word)
                text = pattern.sub(replacement, text)
        return text

    def is_profane(self, text):
        """Check if text contains any profane words as whole words."""
        # Split by common delimiters: spaces, dashes, underscores, dots, commas, etc.
        words = re.split(r'[\s\-_.,;:!?()[\]{}"\'/]+', text.lower())

        # Check if any word in the text matches a word in the blocklist
        for word in words:
            if word and word in self.blocklist:
                return True
        return False

if __name__ == "__main__":
    pf = ProfanityFilter()
    print(pf.filter("This is a test sentence with some bad words like ass."))
