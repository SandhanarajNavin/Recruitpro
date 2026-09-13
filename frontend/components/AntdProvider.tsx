"use client";

import { AntdRegistry } from "@ant-design/nextjs-registry";
import { ConfigProvider, theme } from "antd";

/**
 * antd, configured to match the design rather than replace it.
 *
 * Two things are load-bearing here:
 *
 * `AntdRegistry` collects antd's runtime-generated styles during the server
 * render and inlines them into the HTML. Without it the first paint arrives
 * unstyled and every antd component visibly reflows once the client hydrates.
 *
 * The token block maps antd onto the palette, radii and type scale the rest of
 * the app already uses. antd ships a complete design language — left at its
 * defaults it would introduce a second set of blues, corner radii and font sizes
 * sitting next to the first. Pointing its tokens at ours means an antd Table and
 * a hand-written one look like the same product.
 *
 * The values are literals, not `var(--token)`: antd computes derived colours
 * (hover, active, disabled) arithmetically from these, and it cannot do maths on
 * a CSS custom property. They must therefore be kept in step with globals.css by
 * hand — the comment on each line names its counterpart.
 */
export function AntdProvider({ children }: { children: React.ReactNode }) {
  return (
    <AntdRegistry>
      <ConfigProvider
        theme={{
          algorithm: theme.defaultAlgorithm,
          token: {
            colorPrimary: "#2563eb", // --accent-strong
            colorSuccess: "#17915f", // --good
            colorWarning: "#b47613", // --warn
            colorError: "#c2384c", // --bad
            colorText: "#16203a", // --text
            colorTextSecondary: "#5b6880", // --text-muted
            colorTextTertiary: "#8a94a8", // --text-faint
            colorBorder: "#d3dae6", // --panel-border-strong
            colorBorderSecondary: "#e4e8f0", // --panel-border
            colorBgLayout: "#f4f6fa", // --bg
            colorBgContainer: "#ffffff", // --bg-elevated
            borderRadius: 8, // --radius-sm
            borderRadiusLG: 12, // --radius
            fontFamily: "var(--font-body)",
            fontSize: 14, // body font-size
            controlHeight: 36,
          },
          components: {
            // The app draws its own card chrome, so antd's must not double it.
            Table: { headerBg: "transparent", borderColor: "#e4e8f0" },
            Tabs: { horizontalItemPadding: "9px 12px", horizontalMargin: "0" },
          },
        }}
      >
        {children}
      </ConfigProvider>
    </AntdRegistry>
  );
}
