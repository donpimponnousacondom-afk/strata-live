"""The engineering departments, in sidebar order (F1-F10)."""
from pages.comms import CommsPage
from pages.control import ControlPage
from pages.diag import DiagPage
from pages.direct import DirectPage
from pages.main import MainPage
from pages.maint import MaintPage
from pages.power import PowerPage
from pages.reports import ReportsPage
from pages.shiplog import ShipLogPage
from pages.warp import WarpPage

PAGES = [MainPage, WarpPage, ControlPage, PowerPage, CommsPage, DirectPage, DiagPage, ShipLogPage, ReportsPage,
         MaintPage]
