"use client";

/**
 * Icon set, backed by Material Symbols via `@mui/icons-material`.
 *
 * The names and the `{ size, className }` signature are unchanged from the
 * hand-rolled SVGs these replace, so the twenty files that import them needed no
 * edits. Each glyph is imported by its own deep path rather than from the package
 * root — the root re-exports several thousand icons, and a barrel import pulls the
 * lot into the dev bundle.
 *
 * Sizing goes through `style` rather than `sx`: `sx` runs the emotion runtime for
 * every icon instance, and width/height is all this needs.
 */

import AddRounded from "@mui/icons-material/AddRounded";
import BuildOutlined from "@mui/icons-material/BuildOutlined";
import BusinessCenterOutlined from "@mui/icons-material/BusinessCenterOutlined";
import ChatBubbleOutlineRounded from "@mui/icons-material/ChatBubbleOutlineRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import DescriptionOutlined from "@mui/icons-material/DescriptionOutlined";
import EditOutlined from "@mui/icons-material/EditOutlined";
import GridViewRounded from "@mui/icons-material/GridViewRounded";
import GroupsRounded from "@mui/icons-material/GroupsRounded";
import KeyboardArrowDownRounded from "@mui/icons-material/KeyboardArrowDownRounded";
import LogoutRounded from "@mui/icons-material/LogoutRounded";
import MenuRounded from "@mui/icons-material/MenuRounded";
import NotificationsNoneRounded from "@mui/icons-material/NotificationsNoneRounded";
import PeopleAltOutlined from "@mui/icons-material/PeopleAltOutlined";
import SearchRounded from "@mui/icons-material/SearchRounded";
import SendRounded from "@mui/icons-material/SendRounded";
import StarBorderRounded from "@mui/icons-material/StarBorderRounded";
import TimelineRounded from "@mui/icons-material/TimelineRounded";
import TrendingDownRounded from "@mui/icons-material/TrendingDownRounded";
import TrendingUpRounded from "@mui/icons-material/TrendingUpRounded";
import VisibilityOffOutlined from "@mui/icons-material/VisibilityOffOutlined";
import VisibilityOutlined from "@mui/icons-material/VisibilityOutlined";
import type { SvgIconComponent } from "@mui/icons-material";

type IconProps = { size?: number; className?: string };

/** Wraps a Material icon in the signature the rest of the app already uses. */
function icon(Glyph: SvgIconComponent, fallbackSize: number, name: string) {
  const Wrapped = ({ size = fallbackSize, className }: IconProps) => (
    <Glyph
      className={className}
      // `currentColor` is the whole contract: a nav item colours its icon by
      // colouring itself, which every existing rule relies on.
      style={{ width: size, height: size, fontSize: size, color: "currentColor" }}
      aria-hidden
    />
  );
  Wrapped.displayName = name;
  return Wrapped;
}

export const IconDashboard = icon(GridViewRounded, 20, "IconDashboard");
export const IconCandidates = icon(PeopleAltOutlined, 20, "IconCandidates");
export const IconResumes = icon(DescriptionOutlined, 20, "IconResumes");
export const IconJobs = icon(BusinessCenterOutlined, 20, "IconJobs");
export const IconSearch = icon(SearchRounded, 20, "IconSearch");
export const IconShortlist = icon(StarBorderRounded, 20, "IconShortlist");
export const IconActivity = icon(TimelineRounded, 20, "IconActivity");
export const IconBell = icon(NotificationsNoneRounded, 20, "IconBell");
export const IconMenu = icon(MenuRounded, 22, "IconMenu");
/** Points down by default, matching the rotations `.chev-*` already apply. */
export const IconChevron = icon(KeyboardArrowDownRounded, 18, "IconChevron");
export const IconLogo = icon(GroupsRounded, 30, "IconLogo");
export const IconTrendUp = icon(TrendingUpRounded, 14, "IconTrendUp");
export const IconTrendDown = icon(TrendingDownRounded, 14, "IconTrendDown");
export const IconEye = icon(VisibilityOutlined, 18, "IconEye");
export const IconEyeOff = icon(VisibilityOffOutlined, 18, "IconEyeOff");
export const IconChat = icon(ChatBubbleOutlineRounded, 20, "IconChat");
export const IconSend = icon(SendRounded, 18, "IconSend");
export const IconTool = icon(BuildOutlined, 14, "IconTool");
export const IconPlus = icon(AddRounded, 18, "IconPlus");
export const IconLogout = icon(LogoutRounded, 20, "IconLogout");
export const IconClose = icon(CloseRounded, 16, "IconClose");
export const IconEdit = icon(EditOutlined, 17, "IconEdit");
