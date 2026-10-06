"""Fade lifecycle regressions using the production Lua modules and WoW API doubles.

Run from the repository root:
  uv run --no-project --with-requirements scripts/tests/requirements.txt \
    python -B -m unittest discover -s scripts/tests -p 'test_*.py' -v

These tests cover alpha/hover behavior, not WoW combat protection or taint.
"""

from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime

ADDON = Path(__file__).resolve().parents[2] / "Interface/AddOns/NDui"

WOW_DOUBLES = """
tinsert = table.insert
mod, floor, ceil, min = math.fmod, math.floor, math.ceil, math.min
table.wipe = function(t) for k in pairs(t) do t[k] = nil end end
UIParent = {}
frames = {}
function CreateFrame(_, name, parent)
    local frame = {alpha = 1, shown = true, hover = false, scripts = {}, parent = parent}
    function frame:SetAlpha(alpha) self.alpha = alpha end
    function frame:GetAlpha() return self.alpha end
    function frame:IsShown() return self.shown end
    function frame:IsMouseOver() return self.hover end
    function frame:Show() self.shown = true end
    function frame:Hide() self.shown = false end
    function frame:SetScript(event, callback) self.scripts[event] = callback end
    function frame:SetSize(width, height) self.width, self.height = width, height end
    function frame:GetSize() return self.width, self.height end
    function frame:SetWidth(width) self.width = width end
    function frame:SetHeight(height) self.height = height end
    function frame:ClearAllPoints() end
    function frame:SetPoint(...) self.point = {...} end
    if name then _G[name] = frame end
    table.insert(frames, frame)
    return frame
end
-- Advance the real Fader.lua polling driver, without inspecting its watched list.
function poll()
    for _, frame in ipairs(frames) do
        if frame.shown and frame.scripts.OnUpdate then
            frame.scripts.OnUpdate(frame, .2)
        end
    end
end
Bar = {}
C = {Bars = {margin = 2, padding = 2}, db = {Actionbar = {Enable = true}}}
B = {
    GetModule = function() return Bar end,
    RegisterModule = function() return Bar end,
    RegisterEvent = function() end,
}
ns = {B, C, {}, {}}
LibStub = function() return {} end
C_PetBattles = {IsInBattle = function() return false end}
"""

LAYOUT_FIXTURES = """
-- Font rendering and unrelated login work are outside this fixture's seam.
Bar.UpdateFontSize = function() end
for _, name in ipairs({
    'UpdateOverlays', 'CreateExtrabar', 'CreateLeaveVehicle', 'CreatePetbar',
    'CreateStancebar', 'ReskinBars', 'UpdateBarConfig', 'UpdateVisibility',
    'HideBlizz', 'UpdateCooldownText', 'ReassignBindings',
    'UpdateStanceBar', 'UpdateVehicleButton',
}) do
    Bar[name] = function() end
end
function makeBar(name)
    local frame = CreateFrame('Frame', 'NDui_Action'..name, UIParent)
    frame.mover = CreateFrame('Frame', nil, UIParent)
    frame.mover:Hide()
    frame.buttons = {}
    for i = 1, 12 do frame.buttons[i] = CreateFrame('Button', nil, frame) end
    if name == 'Bar3' then
        frame.child = CreateFrame('Frame', nil, frame)
        frame.child.mover = CreateFrame('Frame', nil, UIParent)
        frame.child.mover:Hide()
    end
    return frame
end
Bar.CreateBars = function() makeBar('Bar3') end
Bar.MicroMenu = function(self)
    if C.db.Actionbar.MicroMenu then self.menubar = CreateFrame('Frame', nil, UIParent) end
end
local db = C.db.Actionbar
for _, name in ipairs({'Bar2', 'Bar3'}) do
    db[name..'Size'], db[name..'Font'] = 32, 12
    db[name..'Num'], db[name..'PerRow'] = 12, 12
    db[name..'Fade'], db[name..'FadeAlpha'] = true, 20
end
"""


class ActionbarFadeTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(encoding=None)
        self.run_lua(WOW_DOUBLES)
        for path in ("Modules/ActionBar/Bars/Bars.lua", "Modules/ActionBar/Fader.lua"):
            self.run_lua('assert(loadstring(...))("NDui", ns)', (ADDON / path).read_bytes())
        self.run_lua(LAYOUT_FIXTURES)

    def run_lua(self, source, *args):
        return self.lua.execute(source.encode(), *args)

    def alpha(self, expression):
        return self.run_lua(f"return {expression}:GetAlpha()")

    def test_bar3_layout_transitions_refresh_hover_with_and_without_reveal_all(self):
        for reveal_all in (False, True):
            with self.subTest(reveal_all=reveal_all):
                self.run_lua("""
                    C.db.Actionbar.FadeRevealAll = ...
                    makeBar('Bar2'); makeBar('Bar3')
                    C.db.Actionbar.Bar3Num = 12
                    Bar:UpdateActionSize('Bar2'); Bar:UpdateActionSize('Bar3')
                    Bar:UpdateFade()
                    C.db.Actionbar.Bar3Num = 0
                    Bar:UpdateActionSize('Bar3')
                    NDui_ActionBar3.child.hover = true
                    poll()
                """, reveal_all)
                self.assertEqual(self.alpha("NDui_ActionBar3"), 1)
                self.assertEqual(self.alpha("NDui_ActionBar2"), 1 if reveal_all else .2)
                self.run_lua("""
                    NDui_ActionBar3.child.hover = false
                    NDui_ActionBar3.hover = true
                    poll()
                """)
                self.assertEqual(self.alpha("NDui_ActionBar3"), 1)
                self.run_lua("""
                    NDui_ActionBar3.hover = false
                    poll()
                """)
                self.assertAlmostEqual(self.alpha("NDui_ActionBar3"), .2)
                self.run_lua("""
                    C.db.Actionbar.Bar3Num = 12
                    Bar:UpdateActionSize('Bar3')
                    NDui_ActionBar3.child.hover = true
                    poll()
                """)
                self.assertAlmostEqual(self.alpha("NDui_ActionBar3"), .2)
                self.assertAlmostEqual(self.alpha("NDui_ActionBar2"), .2)
                self.assertEqual(self.alpha("NDui_ActionBar3.child"), 1)
                # Switching back must re-register the active right-half rectangle.
                self.run_lua("""
                    C.db.Actionbar.Bar3Num = 0
                    Bar:UpdateActionSize('Bar3'); poll()
                """)
                self.assertEqual(self.alpha("NDui_ActionBar3"), 1)

    def test_login_ignores_unused_bar3_child_for_saved_unsplit_layout(self):
        self.run_lua("""
            Bar:OnLogin()
            NDui_ActionBar3.child.hover = true
            poll()
        """)
        self.assertAlmostEqual(self.alpha("NDui_ActionBar3"), .2)
        self.assertEqual(self.alpha("NDui_ActionBar3.child"), 1)

    def test_bulk_layout_update_refreshes_bar3_hover_targets(self):
        # Style-preset imports and login both resize through UpdateAllSize.
        self.run_lua("""
            makeBar('Bar3'); Bar:UpdateAllSize(); Bar:UpdateFade()
            C.db.Actionbar.Bar3Num = 0
            Bar:UpdateAllSize()
            NDui_ActionBar3.child.hover = true
            poll()
        """)
        self.assertEqual(self.alpha("NDui_ActionBar3"), 1)
        self.run_lua("""
            C.db.Actionbar.Bar3Num = 12
            Bar:UpdateAllSize(); poll()
        """)
        self.assertAlmostEqual(self.alpha("NDui_ActionBar3"), .2)

    def test_normal_resize_preserves_fade_and_bar_geometry(self):
        self.run_lua("""
            makeBar('Bar2'); Bar:UpdateActionSize('Bar2'); Bar:UpdateFade()
            C.db.Actionbar.Bar2PerRow = 3
            Bar:UpdateActionSize('Bar2'); poll()
        """)
        self.assertAlmostEqual(self.alpha("NDui_ActionBar2"), .2)
        # 12 buttons of size 32, three columns, margin/padding 2.
        self.assertEqual(self.run_lua("return NDui_ActionBar2.width"), 104)
        self.assertEqual(self.run_lua("return NDui_ActionBar2.height"), 138)
        self.run_lua("NDui_ActionBar2.hover = true; poll()")
        self.assertEqual(self.alpha("NDui_ActionBar2"), 1)

    def test_disabling_bar3_fade_restores_both_frames(self):
        self.run_lua("""
            C.db.Actionbar.Bar3Num = 0
            makeBar('Bar3'); Bar:UpdateActionSize('Bar3'); Bar:UpdateFade(); poll()
        """)
        self.assertAlmostEqual(self.alpha("NDui_ActionBar3"), .2)
        self.run_lua("""
            C.db.Actionbar.Bar3Fade = false
            Bar:UpdateFade(); poll()
        """)
        self.assertEqual(self.alpha("NDui_ActionBar3"), 1)
        self.assertEqual(self.alpha("NDui_ActionBar3.child"), 1)

    def test_menubar_only_login_honors_saved_fade_and_hover(self):
        for opacity, expected in ((0, 0), (20, .2), (100, 1)):
            with self.subTest(opacity=opacity):
                self.setUp()
                self.run_lua("""
                    local db = C.db.Actionbar
                    db.Enable, db.MicroMenu = false, true
                    db.MicroMenuFade, db.MicroMenuFadeAlpha = true, ...
                    -- The disabled actionbar path must never create actionbars.
                    Bar.CreateBars = function() error('actionbars are disabled') end
                    Bar:OnLogin()
                """, opacity)
                self.assertAlmostEqual(self.alpha("Bar.menubar"), expected)
                self.run_lua("Bar.menubar.hover = true; poll()")
                self.assertEqual(self.alpha("Bar.menubar"), 1)
                self.run_lua("Bar.menubar.hover = false; poll()")
                self.assertAlmostEqual(self.alpha("Bar.menubar"), expected)
                self.run_lua("C.db.Actionbar.MicroMenuFade = false; Bar:UpdateFade(); poll()")
                self.assertEqual(self.alpha("Bar.menubar"), 1)

    def test_menubar_only_login_with_fade_disabled_leaves_full_opacity(self):
        self.run_lua("""
            local db = C.db.Actionbar
            db.Enable, db.MicroMenu = false, true
            db.MicroMenuFade, db.MicroMenuFadeAlpha = false, 0
            Bar:OnLogin(); poll()
        """)
        self.assertEqual(self.alpha("Bar.menubar"), 1)

    def test_menubar_only_login_with_menu_disabled_leaves_blizzard_menu_untouched(self):
        self.run_lua("""
            local db = C.db.Actionbar
            db.Enable, db.MicroMenu = false, false
            db.MicroMenuFade, db.MicroMenuFadeAlpha = true, 0
            CreateFrame('Frame', 'MicroMenu', UIParent):SetAlpha(.7)
            CreateFrame('Frame', 'BagsBar', UIParent):SetAlpha(.8)
            Bar:OnLogin(); poll()
        """)
        self.assertIsNone(self.run_lua("return Bar.menubar"))
        self.assertAlmostEqual(self.alpha("MicroMenu"), .7)
        self.assertAlmostEqual(self.alpha("BagsBar"), .8)


if __name__ == "__main__":
    unittest.main()
