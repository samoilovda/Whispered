# UI components package.
# No re-exports: every consumer imports from the concrete module
# (e.g. `from ui.main_window import MainWindow`), and eager imports here
# would pull the whole UI tree in on first `import ui.<anything>`.
