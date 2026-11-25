from src.asgi import server


def main() -> None:
    server.serve()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()