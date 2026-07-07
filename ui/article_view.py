"""
Whisper Fedora UI - Article View Widget
Display and export generated articles with tabbed interface
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QTextEdit, QPushButton, QLabel, QFileDialog, QApplication, QMessageBox
)
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont

from article_generator import (
    Article, ArticleFormat, ARTICLE_FORMAT_INFO,
    export_article_md, export_article_html, export_all_articles
)


class ArticleTab(QWidget):
    """Single article display tab."""

    copy_requested = pyqtSignal()
    export_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._article: Article | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        # Title and stats row
        header = QHBoxLayout()

        self.title_label = QLabel("No article")
        self.title_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #e0e0e0;")
        self.title_label.setWordWrap(True)
        header.addWidget(self.title_label, stretch=1)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #888; font-size: 11px;")
        header.addWidget(self.stats_label)

        layout.addLayout(header)

        # Content area
        self.content_edit = QTextEdit()
        self.content_edit.setReadOnly(True)
        self.content_edit.setFont(QFont("SF Mono", 12))
        layout.addWidget(self.content_edit, stretch=1)

        # Action buttons
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.copy_btn = QPushButton("📋 Copy")
        self.copy_btn.clicked.connect(self._on_copy)
        self.copy_btn.setEnabled(False)
        actions.addWidget(self.copy_btn)

        self.export_md_btn = QPushButton("💾 Export .md")
        self.export_md_btn.clicked.connect(lambda: self._on_export('md'))
        self.export_md_btn.setEnabled(False)
        actions.addWidget(self.export_md_btn)

        self.export_html_btn = QPushButton("🌐 Export .html")
        self.export_html_btn.clicked.connect(lambda: self._on_export('html'))
        self.export_html_btn.setEnabled(False)
        actions.addWidget(self.export_html_btn)

        actions.addStretch()

        # Quality score
        self.score_label = QLabel("")
        self.score_label.setStyleSheet("color: #888; font-size: 11px;")
        actions.addWidget(self.score_label)

        layout.addLayout(actions)

    def set_article(self, article: Article):
        """Set the article to display."""
        self._article = article

        self.title_label.setText(article.title)
        self.content_edit.setMarkdown(article.content)
        self.stats_label.setText(f"{article.word_count} words")

        if article.quality_score > 0:
            self.score_label.setText(f"Quality: {article.quality_score:.1f}/10")

        # Enable buttons
        self.copy_btn.setEnabled(True)
        self.export_md_btn.setEnabled(True)
        self.export_html_btn.setEnabled(True)

    def clear(self):
        """Clear the article display."""
        self._article = None
        self.title_label.setText("No article")
        self.content_edit.clear()
        self.stats_label.setText("")
        self.score_label.setText("")

        self.copy_btn.setEnabled(False)
        self.export_md_btn.setEnabled(False)
        self.export_html_btn.setEnabled(False)

    def get_article(self) -> Article | None:
        """Get the current article."""
        return self._article

    def _on_copy(self):
        """Copy article content to clipboard."""
        if self._article:
            clipboard = QApplication.clipboard()
            clipboard.setText(self._article.content)
            self.copy_requested.emit()

    def _on_export(self, format: str):
        """Export article to file."""
        if not self._article:
            return

        # Create safe filename
        safe_title = "".join(c if c.isalnum() or c in ' -_' else '_' for c in self._article.title)
        safe_title = safe_title[:50].strip()

        if format == 'md':
            filepath, _ = QFileDialog.getSaveFileName(
                self, "Export Markdown", f"{safe_title}.md",
                "Markdown Files (*.md);;All Files (*)"
            )
            if filepath:
                try:
                    export_article_md(self._article, filepath)
                    self.export_requested.emit()
                except Exception as e:
                    QMessageBox.critical(self, "Export Error", str(e))

        elif format == 'html':
            filepath, _ = QFileDialog.getSaveFileName(
                self, "Export HTML", f"{safe_title}.html",
                "HTML Files (*.html);;All Files (*)"
            )
            if filepath:
                try:
                    export_article_html(self._article, filepath)
                    self.export_requested.emit()
                except Exception as e:
                    QMessageBox.critical(self, "Export Error", str(e))


class ArticleView(QWidget):
    """Tabbed view for displaying multiple article formats."""

    copy_done = pyqtSignal()
    export_done = pyqtSignal(str)  # filename

    def __init__(self, parent=None):
        super().__init__(parent)
        self._articles: dict[ArticleFormat, Article] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Tab widget for different formats
        self.tabs = QTabWidget()

        # Create tabs for each format
        self.format_tabs: dict[ArticleFormat, ArticleTab] = {}

        for fmt in ArticleFormat:
            info = ARTICLE_FORMAT_INFO[fmt]
            tab = ArticleTab()
            tab.copy_requested.connect(lambda: self.copy_done.emit())
            tab.export_requested.connect(lambda: self.export_done.emit("exported"))

            self.tabs.addTab(tab, f"{info['icon']} {info['name'].split()[1]}")
            self.format_tabs[fmt] = tab

        layout.addWidget(self.tabs)

        # Export all button
        export_all_layout = QHBoxLayout()
        export_all_layout.setContentsMargins(0, 8, 0, 0)
        export_all_layout.addStretch()

        self.export_all_btn = QPushButton("📦 Export All to Folder")
        self.export_all_btn.setProperty("variant", "primary")
        self.export_all_btn.clicked.connect(self._on_export_all)
        self.export_all_btn.setEnabled(False)
        export_all_layout.addWidget(self.export_all_btn)

        layout.addLayout(export_all_layout)

    def set_article(self, article: Article):
        """Set a single article (adds to the appropriate tab)."""
        self._articles[article.format] = article

        if article.format in self.format_tabs:
            self.format_tabs[article.format].set_article(article)
            # Switch to the newly added article's tab
            tab_index = list(ArticleFormat).index(article.format)
            self.tabs.setCurrentIndex(tab_index)

        self._update_export_all_button()

    def set_articles(self, articles: list[Article]):
        """Set multiple articles at once."""
        for article in articles:
            self._articles[article.format] = article
            if article.format in self.format_tabs:
                self.format_tabs[article.format].set_article(article)

        self._update_export_all_button()

    def clear(self):
        """Clear all articles."""
        self._articles.clear()
        for tab in self.format_tabs.values():
            tab.clear()
        self.export_all_btn.setEnabled(False)

    def get_articles(self) -> list[Article]:
        """Get all current articles."""
        return list(self._articles.values())

    def has_articles(self) -> bool:
        """Check if any articles are loaded."""
        return len(self._articles) > 0

    def _update_export_all_button(self):
        """Update export all button state."""
        self.export_all_btn.setEnabled(len(self._articles) > 1)

    def _on_export_all(self):
        """Export all articles to a folder."""
        if not self._articles:
            return

        directory = QFileDialog.getExistingDirectory(self, "Select Export Folder")
        if directory:
            try:
                articles = list(self._articles.values())
                created_files = export_all_articles(articles, directory)
                self.export_done.emit(f"Exported {len(created_files)} files")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))


class CleanedTextView(QWidget):
    """View for displaying cleaned/processed text."""

    copy_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cleaned_text = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        # Stats row
        stats = QHBoxLayout()

        self.stats_label = QLabel("No processed text")
        self.stats_label.setStyleSheet("color: #888; font-size: 12px;")
        stats.addWidget(self.stats_label)

        stats.addStretch()

        self.improvement_label = QLabel("")
        self.improvement_label.setStyleSheet("color: #22c55e; font-size: 11px;")
        stats.addWidget(self.improvement_label)

        layout.addLayout(stats)

        # Content
        self.content_edit = QTextEdit()
        self.content_edit.setReadOnly(True)
        layout.addWidget(self.content_edit, stretch=1)

        # Actions
        actions = QHBoxLayout()

        self.copy_btn = QPushButton("📋 Copy Cleaned Text")
        self.copy_btn.clicked.connect(self._on_copy)
        self.copy_btn.setEnabled(False)
        actions.addWidget(self.copy_btn)

        actions.addStretch()

        layout.addLayout(actions)

    def set_text(self, cleaned_text: str, original_length: int = 0,
                 removed_fillers: int = 0, paragraphs: int = 0):
        """Set the cleaned text with stats."""
        self._cleaned_text = cleaned_text
        self.content_edit.setPlainText(cleaned_text)

        # Update stats
        word_count = len(cleaned_text.split())
        self.stats_label.setText(f"{word_count} words • {paragraphs} paragraphs")

        if original_length > 0 and len(cleaned_text) < original_length:
            reduction = ((original_length - len(cleaned_text)) / original_length) * 100
            self.improvement_label.setText(
                f"✨ Removed {removed_fillers} fillers • {reduction:.0f}% shorter"
            )

        self.copy_btn.setEnabled(True)

    def clear(self):
        """Clear the view."""
        self._cleaned_text = ""
        self.content_edit.clear()
        self.stats_label.setText("No processed text")
        self.improvement_label.setText("")
        self.copy_btn.setEnabled(False)

    def get_text(self) -> str:
        """Get the cleaned text."""
        return self._cleaned_text

    def _on_copy(self):
        """Copy cleaned text to clipboard."""
        if self._cleaned_text:
            clipboard = QApplication.clipboard()
            clipboard.setText(self._cleaned_text)
            self.copy_requested.emit()
