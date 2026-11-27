"""Prepare Power House's two public files for Cloudflare Workers Builds."""
from pathlib import Path
from shutil import copyfile


def build(root):
    output = root / "dist"
    assets = (
        Path(".well-known/appspecific/com.tesla.3p.public-key.pem"),
        Path("callback.html"),
    )
    unexpected = [
        path for path in output.rglob("*")
        if path.is_symlink() or (path.is_file() and path.relative_to(output) not in assets)
    ]
    if output.is_symlink() or unexpected:
        raise ValueError("Unexpected files in dist. Use a clean build directory.")
    for relative in assets:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(root / relative, target)


if __name__ == "__main__":
    build(Path(__file__).resolve().parent)
