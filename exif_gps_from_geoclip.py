import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
import geopy.distance


from PIL import Image
import piexif
import fire
from geoclip import GeoCLIP
from tqdm import tqdm

_LOGGER = logging.getLogger(__name__)

VERSION = "0.1.0"
PROCESSED_TAG_INDEX = 0xfe70
assert PROCESSED_TAG_INDEX not in piexif.ExifIFD.__dict__.values()
piexif.TAGS["Exif"][PROCESSED_TAG_INDEX] = {"name": "ExifGPSFromGeoClip", "type":piexif.TYPES.Undefined}
PROCESSED_TAG_NON_VARIABLE = "exif_gps_from_geoclip"
PROCESSED_TAG = f"{PROCESSED_TAG_NON_VARIABLE}_v{VERSION}"

from fractions import Fraction

def distance(lat1, lon1, lat2, lon2):
    coords_1 = (lat1, lon1)
    coords_2 = (lat2, lon2)
    return geopy.distance.geodesic(coords_1, coords_2).km

def to_deg(value, loc):
    """convert decimal coordinates into degrees, munutes and seconds tuple
    Keyword arguments: value is float gps-value, loc is direction list ["S", "N"] or ["W", "E"]
    return: tuple like (25, 13, 48.343 ,'N')
    """
    if value < 0:
        loc_value = loc[0]
    elif value > 0:
        loc_value = loc[1]
    else:
        loc_value = ""
    abs_value = abs(value)
    deg =  int(abs_value)
    t1 = (abs_value-deg)*60
    min = int(t1)
    sec = round((t1 - min)* 60, 5)
    return (deg, min, sec, loc_value)


def change_to_rational(number):
    """convert a number to rantional
    Keyword arguments: number
    return: tuple like (1, 2), (numerator, denominator)
    """
    f = Fraction(str(number))
    return (f.numerator, f.denominator)


def gps_ifd(lat, lng, altitude=None):
    """Adds GPS position as EXIF metadata
    Keyword arguments:
    file_name -- image file
    lat -- latitude (as float)
    lng -- longitude (as float)
    altitude -- altitude (as float)
    """
    lat_deg = to_deg(lat, ["S", "N"])
    lng_deg = to_deg(lng, ["W", "E"])

    exiv_lat = (change_to_rational(lat_deg[0]), change_to_rational(lat_deg[1]), change_to_rational(lat_deg[2]))
    exiv_lng = (change_to_rational(lng_deg[0]), change_to_rational(lng_deg[1]), change_to_rational(lng_deg[2]))

    gps_ifd = {
        piexif.GPSIFD.GPSVersionID: (2, 0, 0, 0),
        piexif.GPSIFD.GPSLatitudeRef: lat_deg[3],
        piexif.GPSIFD.GPSLatitude: exiv_lat,
        piexif.GPSIFD.GPSLongitudeRef: lng_deg[3],
        piexif.GPSIFD.GPSLongitude: exiv_lng,
    }
    if altitude is not None:
        gps_ifd[piexif.GPSIFD.GPSAltitudeRef] = 1,
        gps_ifd[piexif.GPSIFD.GPSAltitude] = change_to_rational(round(altitude))

    exif_dict = gps_ifd
    return exif_dict

def update_exif_date(image_path: Path, dry_run: bool = False, update: bool = False, force: bool = False, max_distance: int = 20, top_k: int = 5) -> bool:
    # Open the image
    try:
        img = Image.open(image_path)
    except Exception as e:
        _LOGGER.debug(f"Error opening {image_path}: {str(e)}")
        if "cannot identify image file" in str(e):
            _LOGGER.debug(f"Skipping non-image file: {image_path}")
        else:
            _LOGGER.warning(f"Error opening {image_path}: {str(e)}")
        return False
    try:

        # Check if EXIF data exists
        if "exif" in img.info:
            exif_dict = piexif.load(img.info["exif"])
        else:
            exif_dict = {"0th": {}, "1st": {}, "Exif": {}, "GPS": {}, "Interop": {}}

        # Check if GPS data exists
        if piexif.GPSIFD.GPSLatitude not in exif_dict["GPS"] or (
           exif_dict["GPS"].get(PROCESSED_TAG_INDEX, b"").decode("ascii").startswith(PROCESSED_TAG_NON_VARIABLE) and update
        ) or force:
            top_pred_gps, top_pred_prob = model.predict(str(image_path), top_k=top_k)

            # Sanity check: maximum n km between top prediction and the rest with sufficient probability
            top_lat, top_lon = top_pred_gps[0]
            top_lat, top_lon = float(top_lat.item()), float(top_lon.item())
            for i in range(1, top_k):
                lat, lon = top_pred_gps[i]
                dist = distance(lat, lon, top_lat, top_lon)
                if dist > max_distance:
                    _LOGGER.debug(f"Skipping {image_path}: Top prediction too far from other predictions ({dist:.2f} km)")
                    return False
            if dry_run:
                _LOGGER.info(f"Would update EXIF GPS for {image_path} to {(top_lat, top_lon)}")
                return True

            # Set the GPS data
            exif_dict["GPS"].update(gps_ifd(top_lat, top_lon))
            # Add processed tag
            exif_dict["Exif"][PROCESSED_TAG_INDEX] = PROCESSED_TAG.encode("ascii")

            # Save the updated EXIF data (atomic, to avoid corrupting the image)
            exif_bytes = piexif.dump(exif_dict)
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=image_path.suffix, dir=image_path.parent
            ) as tmp:
                img.save(tmp.name, exif=exif_bytes)

                os.replace(tmp.name, image_path)
            _LOGGER.info(f"Updated EXIF GPS for {image_path} to {(top_lat, top_lon)}")
            return True
        else:
            _LOGGER.debug(f"EXIF date already set for {image_path}")

    except Exception as e:
        _LOGGER.warning(f"Error processing {image_path}: {str(e)}")
    return False

def process_directory(
    directory: str, verbosity: int = logging.INFO, wet_run: bool = False, update: bool= False, force: bool = False,
        top_k: int = 5, max_distance: int = 20
):
    """
    Process all images in the given directory and update their EXIF date based on filename, if missing
    :param directory: Directory containing images
    :param verbosity: Logging verbosity level
    :param wet_run: Perform the actual update (default is dry run)
    :param update: Overwrite tags that were written by us
    :param force: Force update even if DateTimeOriginal tag is already set by external software
    :param top_k: Number of top predictions to check
    :param max_distance: Maximum distance between top prediction and other predictions
    """
    # cursed logging setup
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    handler.setLevel(verbosity)
    _LOGGER.setLevel(verbosity)
    _LOGGER.addHandler(handler)

    # actual processing
    iter = Path(directory).walk()
    if verbosity > logging.INFO:
        # should add a progress bar if verbosity is high
        iter = tqdm(iter)
    updated_dirs = set()

    global model
    model = GeoCLIP()

    for dir_path, dir_names, file_names in iter:
        _LOGGER.info(f"Processing directory: {dir_path}")
        for filename in sorted(file_names):
            if Path(filename).suffix.lower() not in [
                ".jpg",
                ".jpeg",
                ".tif",
                ".webp",
                ".tiff",
                ".png",
            ]:
                continue
            _LOGGER.debug(f"Processing file: {filename}")
            image_path = dir_path / filename
            updated = update_exif_date(image_path, not wet_run, update, force, top_k=top_k, max_distance=max_distance)
            if updated:
                updated_dirs.add(dir_path)
    _LOGGER.info("Done!")
    if updated_dirs:
        _LOGGER.info("Dumping updated directories to stdout")
        for dir in updated_dirs:
            print(dir)


# Usage
if __name__ == "__main__":
    fire.Fire(process_directory)
