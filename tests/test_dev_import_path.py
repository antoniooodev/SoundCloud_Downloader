from pathlib import Path

import soundcloud_downloader


def test_package_import_resolves_to_source_tree() -> None:
    package_path = Path(soundcloud_downloader.__file__).resolve()
    source_package = Path(__file__).resolve().parents[1] / "src" / "soundcloud_downloader"

    assert package_path.is_relative_to(source_package)
