# Retained measurement archives

- measure_5.zip: accepted 22:09 MPPT run, older available raw iterations, and comparison results. Local working data remains in ../measure_5/.
- measure_23_new.zip: full combined IV reference, including raw sweeps, source manifests and failed-attempt history. Local working data remains in ../measure_23_new/.

Both full ZIPs passed ZIP integrity checks. Per-file and archive SHA-256 hashes are in the corresponding *_manifest.json files.

Notion attachments use ordinary numbered part ZIPs below 5 MB: 29 for measurement 5, 14 for measurement 23. All parts were uploaded and attached to the corresponding page's CSV data property; notion_attachments.json records the receipts.

To restore from Notion, download every numbered ZIP part for the dataset. Extract them all into one shared folder, merging duplicate instructions and restore-script files. Run `python RESTORE.py` with Python 3.11 or newer, then unzip the reconstructed archive. The script verifies the full archive SHA-256. The local complete ZIPs here do not require reconstruction.

The obsolete ../measure_23 directory and its old Notion attachment were removed after the replacement was uploaded. No MPPT iteration available at archive preparation was discarded.
