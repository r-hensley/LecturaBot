"""Compatibility wrapper for the renamed merged-catalog builder."""

if __package__:
    from .build_catalog import main
else:
    from build_catalog import main


if __name__ == "__main__":
    main()
