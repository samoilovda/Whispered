"""
Whisper Fedora UI - SVG Vector Icons
Centralized icon provider using inline SVG definitions for resolution-independent graphics.
"""

from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtCore import QByteArray, Qt, QSize
from PyQt6.QtWidgets import QLabel


# SVG icon definitions using Feather-inspired designs
# All icons use viewBox="0 0 24 24" for consistency
ICONS = {
    # App Logo
    'microphone': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 1C10.34 1 9 2.34 9 4V12C9 13.66 10.34 15 12 15C13.66 15 15 13.66 15 12V4C15 2.34 13.66 1 12 1Z" fill="currentColor"/>
        <path d="M19 10V12C19 15.87 15.87 19 12 19C8.13 19 5 15.87 5 12V10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        <path d="M12 19V23M8 23H16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>''',
    
    # Music/Audio file
    'music': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M9 18V5L21 3V16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="6" cy="18" r="3" stroke="currentColor" stroke-width="2"/>
        <circle cx="18" cy="16" r="3" stroke="currentColor" stroke-width="2"/>
    </svg>''',
    
    # Document/File
    'file': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M14 2H6C5.46957 2 4.96086 2.21071 4.58579 2.58579C4.21071 2.96086 4 3.46957 4 4V20C4 20.5304 4.21071 21.0391 4.58579 21.4142C4.96086 21.7893 5.46957 22 6 22H18C18.5304 22 19.0391 21.7893 19.4142 21.4142C19.7893 21.0391 20 20.5304 20 20V8L14 2Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M14 2V8H20" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M16 13H8M16 17H8M10 9H8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>''',
    
    # Checkmark
    'check': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M20 6L9 17L4 12" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>''',
    
    # Check circle (success)
    'check_circle': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M22 11.08V12C21.9988 14.1564 21.3005 16.2547 20.0093 17.9818C18.7182 19.709 16.9033 20.9725 14.8354 21.5839C12.7674 22.1953 10.5573 22.1219 8.53447 21.3746C6.51168 20.6273 4.78465 19.2461 3.61096 17.4371C2.43727 15.628 1.87979 13.4881 2.02168 11.3363C2.16356 9.18455 2.99721 7.13631 4.39828 5.49706C5.79935 3.85781 7.69279 2.71537 9.79619 2.24013C11.8996 1.7649 14.1003 1.98232 16.07 2.85999" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M22 4L12 14.01L9 11.01" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>''',
    
    # Close/X
    'close': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>''',
    
    # Rocket (GPU acceleration)
    'rocket': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M4.5 16.5C3 18 3 21 3 21C3 21 6 21 7.5 19.5C8.32843 18.6716 8.32843 17.3284 7.5 16.5C6.67157 15.6716 5.32843 15.6716 4.5 16.5Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M14.5 4C14.5 4 13.5 6 13.5 8.5C13.5 11 16 16 16 16L20 12C20 12 15 9.5 15 7C15 4.5 17 3.5 17 3.5L14.5 4Z" fill="currentColor" fill-opacity="0.2"/>
        <path d="M21.174 6.81201C22.272 9.00801 22.392 11.58 21.498 14.094C21.33 14.574 20.85 14.856 20.358 14.772L17 14.172L14.172 17L14.772 20.358C14.856 20.85 14.574 21.33 14.094 21.498C11.58 22.392 9.00803 22.272 6.81203 21.174" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M22 2L15 9M22 2L18.5 2M22 2L22 5.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="12" cy="12" r="2" stroke="currentColor" stroke-width="2"/>
    </svg>''',
    
    # CPU
    'cpu': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="4" y="4" width="16" height="16" rx="2" stroke="currentColor" stroke-width="2"/>
        <rect x="9" y="9" width="6" height="6" stroke="currentColor" stroke-width="2"/>
        <path d="M9 1V4M15 1V4M9 20V23M15 20V23M1 9H4M1 15H4M20 9H23M20 15H23" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>''',
    
    # Apple logo (simplified)
    'apple': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2C13.5 2 15 3 15.5 4.5C14 4.5 12.5 5.5 12.5 7.5C12.5 9 13.5 10 15 10C14.5 12 13 13 12 13C11 13 10 12.5 9 12.5C8 12.5 7 13 6 13C4 13 2 10 2 7C2 4 4 2 6 2C7 2 8 2.5 9 2.5C10 2.5 11 2 12 2Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M17 6C17 6 18.5 4.5 18.5 3C18.5 2 18 1 18 1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        <path d="M12 14C12 14 8 16 8 20C8 22 10 23 12 23C14 23 16 22 16 20C16 16 12 14 12 14Z" fill="currentColor" fill-opacity="0.3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>''',
    
    # Clock/Time
    'clock': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/>
        <path d="M12 6V12L16 14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>''',
    
    # Clipboard/Copy
    'clipboard': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="9" y="9" width="13" height="13" rx="2" stroke="currentColor" stroke-width="2"/>
        <path d="M5 15H4C2.89543 15 2 14.1046 2 13V4C2 2.89543 2.89543 2 4 2H13C14.1046 2 15 2.89543 15 4V5" stroke="currentColor" stroke-width="2"/>
    </svg>''',
    
    # Save/Download
    'save': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M19 21H5C3.89543 21 3 20.1046 3 19V5C3 3.89543 3.89543 3 5 3H16L21 8V19C21 20.1046 20.1046 21 19 21Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M17 21V13H7V21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M7 3V8H15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>''',
    
    # Play
    'play': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <polygon points="5,3 19,12 5,21" fill="currentColor"/>
    </svg>''',
    
    # Fire (max performance)
    'fire': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 22C16.4183 22 20 18.4183 20 14C20 10 17 7 16 4C15 6 13 7 12 7C11 7 9.5 6 9 5C8.5 4 8 2 8 2C6 4 4 8 4 12C4 18.4183 7.58172 22 12 22Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M12 22C14.2091 22 16 19.9853 16 17.5C16 15.5 14.5 14 14 12C13 14 12 14.5 11 14.5C10.5 14.5 10 14 9.5 13C9 14 8 16 8 17.5C8 19.9853 9.79086 22 12 22Z" fill="currentColor" fill-opacity="0.3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>''',
    
    # Balance/Scale
    'balance': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 3V21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        <path d="M5 6L12 3L19 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M2 12L5 6L8 12C8 14 6.5 15 5 15C3.5 15 2 14 2 12Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M16 12L19 6L22 12C22 14 20.5 15 19 15C17.5 15 16 14 16 12Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M8 21H16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>''',
    
    # Leaf (efficiency)
    'leaf': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M11 20C11 20 4 17 4 10C4 4 10 2 16 2C16 8 18 12 20 14C20 18 16 20 11 20Z" fill="currentColor" fill-opacity="0.2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M8 16C8 16 10 14 14 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        <path d="M2 22L8 16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>''',
    
    # Error/Alert
    'error': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/>
        <path d="M15 9L9 15M9 9L15 15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>''',
    
    # Upload
    'upload': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M21 15V19C21 20.1046 20.1046 21 19 21H5C3.89543 21 3 20.1046 3 19V15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M17 8L12 3L7 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M12 3V15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>''',
    
    # Folder
    'folder': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M22 19C22 20.1046 21.1046 21 20 21H4C2.89543 21 2 20.1046 2 19V5C2 3.89543 2.89543 3 4 3H9L11 6H20C21.1046 6 22 6.89543 22 8V19Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>''',
    
    # User/Person (for speaker labels)
    'user': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M20 21V19C20 16.7909 18.2091 15 16 15H8C5.79086 15 4 16.7909 4 19V21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="12" cy="7" r="4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>''',
    
    # Users/People (for diarization)
    'users': '''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M17 21V19C17 16.7909 15.2091 15 13 15H5C2.79086 15 1 16.7909 1 19V21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="9" cy="7" r="4" stroke="currentColor" stroke-width="2"/>
        <path d="M23 21V19C22.9986 17.1771 21.765 15.5857 20 15.13" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        <path d="M16 3.13C17.7699 3.58317 19.0078 5.17805 19.0078 7.005C19.0078 8.83195 17.7699 10.4268 16 10.88" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>''',
}


def get_icon(name: str, color: str = '#888888', size: int = 24) -> QIcon:
    """
    Generate a QIcon from SVG with specified color and size.
    
    Args:
        name: Icon name from ICONS dictionary
        color: Hex color string (e.g., '#ffffff')
        size: Icon size in pixels
        
    Returns:
        QIcon object ready to use
    """
    svg_data = ICONS.get(name, '')
    if not svg_data:
        return QIcon()
    
    # Replace currentColor with the specified color
    svg_data = svg_data.replace('currentColor', color)
    
    # Create renderer from SVG data
    renderer = QSvgRenderer(QByteArray(svg_data.encode()))
    
    # Create transparent pixmap
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    
    # Render SVG to pixmap
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    
    return QIcon(pixmap)


def get_pixmap(name: str, color: str = '#888888', size: int = 24) -> QPixmap:
    """
    Generate a QPixmap from SVG with specified color and size.
    
    Args:
        name: Icon name from ICONS dictionary
        color: Hex color string (e.g., '#ffffff')
        size: Icon size in pixels
        
    Returns:
        QPixmap object ready to use
    """
    svg_data = ICONS.get(name, '')
    if not svg_data:
        return QPixmap()
    
    # Replace currentColor with the specified color
    svg_data = svg_data.replace('currentColor', color)
    
    # Create renderer from SVG data
    renderer = QSvgRenderer(QByteArray(svg_data.encode()))
    
    # Create transparent pixmap
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    
    # Render SVG to pixmap
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    
    return pixmap


class IconLabel(QLabel):
    """
    QLabel subclass that displays an SVG icon.
    Supports dynamic color and size changes.
    """
    
    def __init__(self, icon_name: str, color: str = '#888888', size: int = 24, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._color = color
        self._size = size
        self._update_icon()
    
    def _update_icon(self):
        """Update the displayed icon."""
        pixmap = get_pixmap(self._icon_name, self._color, self._size)
        self.setPixmap(pixmap)
        self.setFixedSize(self._size, self._size)
    
    def set_icon(self, name: str):
        """Change the icon."""
        self._icon_name = name
        self._update_icon()
    
    def set_color(self, color: str):
        """Change the icon color."""
        self._color = color
        self._update_icon()
    
    def set_size(self, size: int):
        """Change the icon size."""
        self._size = size
        self._update_icon()


# Color constants for consistent theming
class IconColors:
    """Standard icon colors matching the app theme."""
    DEFAULT = '#888888'
    PRIMARY = '#6366f1'  # Indigo
    SUCCESS = '#22c55e'  # Green
    WARNING = '#f59e0b'  # Amber
    ERROR = '#f87171'    # Red
    WHITE = '#ffffff'
    MUTED = '#666666'
