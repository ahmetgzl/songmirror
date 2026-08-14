import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS } from "../theme";
import { FONT } from "../fonts";
import { enter, rise } from "../util/anim";

const SERVICES = [
  { name: "Spotify", color: COLORS.spotify },
  { name: "TIDAL", color: COLORS.tidal },
  { name: "Qobuz", color: COLORS.qobuz },
  { name: "Deezer", color: COLORS.deezer },
  { name: "Amazon Music", color: COLORS.amazon },
  { name: "Apple Music", color: COLORS.apple },
  { name: "YouTube Music", color: COLORS.ytmusic },
];

// A row of brand-dot chips for every supported streaming service.
export const ServiceRow: React.FC<{ delay?: number }> = ({ delay = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <div
      style={{
        display: "flex",
        gap: 16,
        alignItems: "center",
        justifyContent: "center",
        flexWrap: "wrap",
      }}
    >
      {SERVICES.map((s, i) => {
        const p = enter(frame, fps, delay + i * 5);
        return (
          <div
            key={s.name}
            style={{
              opacity: p,
              transform: `translateY(${rise(p, 14)}px)`,
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "12px 22px",
              borderRadius: 999,
              background: "rgba(255,255,255,0.04)",
              border: `1px solid ${COLORS.panelBorder}`,
            }}
          >
            <span
              style={{
                width: 12,
                height: 12,
                borderRadius: 6,
                background: s.color,
                boxShadow: `0 0 14px ${s.color}`,
              }}
            />
            <span
              style={{
                fontFamily: FONT,
                fontWeight: 600,
                fontSize: 24,
                color: COLORS.text,
              }}
            >
              {s.name}
            </span>
          </div>
        );
      })}
    </div>
  );
};
