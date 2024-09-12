# Update file EXIF GPS Tags from GEOClip suggestions

Sometimes, a file is simply missing the GPS tags.
This script uses [GeoClip](https://github.com/VicenteVivan/geo-clip) to suggest GPS latitude and longtitude  for a file and updates the EXIF GPS tags accordingly.
The GPS tags are only written when the model exceeds a certain confidence level (default 80%).

## Installation

```bash
git clone https://github.com/nielstron/exif_gps_from_geoclip.git
cd exif_gps_from_geoclip 
pip install -r requirements.txt
```

## Usage

In order to see what changes _would_ be made, run the script with the default flags:

```bash
python exif_gps_from_geoclip.py /path/to/photos
```

If you're happy with the changes, run the script with the `--wet_run True` flag:

```bash
python exif_gps_from_geoclip.py /path/to/photos --wet_run True
```


## Re-indexing (Nextcloud)

When using Nextcloud / Memories, you may want to re-index the photos after updating the EXIF date. This can be done by running the following command:

```bash
php occ memories:index --force --folder /path/to/photos
```

Because this command can take longer, exif_from_filename will print the folders that contain actual changes at the end of the run.