from __future__ import annotations

from .gui import CrawlerApp


def main() -> None:
    app = CrawlerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
