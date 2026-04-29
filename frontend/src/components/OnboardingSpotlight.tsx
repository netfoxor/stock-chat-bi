import { Button, Typography } from "antd";
import type { CSSProperties } from "react";

export type SpotRect = { top: number; left: number; width: number; height: number };

export function domRectToSpot(r: DOMRect): SpotRect {
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

/** 贴近「洞」上方的引导条（空间不足时紧贴洞下缘） */
export function hintStyleAdjacentToHole(hole: SpotRect): CSSProperties {
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1200;
  const gap = 10;
  const approxLabelH = 56;
  const cx = hole.left + hole.width / 2;
  const leftPct = Math.min(Math.max((cx / vw) * 100, 8), 92);

  const spaceAbove = hole.top;
  const preferAbove = spaceAbove >= approxLabelH + gap + 12;

  let topPx: number;
  if (preferAbove) {
    topPx = Math.max(12, hole.top - gap - approxLabelH);
  } else {
    topPx = Math.min(hole.top + hole.height + gap, Math.max(14, vh - approxLabelH - 12));
  }

  return {
    position: "fixed",
    left: `${leftPct}%`,
    transform: "translateX(-50%)",
    top: topPx,
    maxWidth: Math.min(440, vw - 24),
    textAlign: "center" as const,
  };
}

const fallbackBottomHintStyle: CSSProperties = {
  position: "fixed",
  left: "50%",
  transform: "translateX(-50%)",
  bottom: "max(22vh, 160px)",
  maxWidth: 440,
  width: "calc(100vw - 48px)",
  textAlign: "center" as const,
};

function OnboardingHintBubble(props: {
  title: string;
  zForeground: number;
  style: CSSProperties;
  onSkip?: () => void;
}) {
  return (
    <div
      role="note"
      style={{
        ...props.style,
        zIndex: props.zForeground,
        pointerEvents: "auto",
      }}
    >
      <Typography.Text style={{ color: "#fff", fontSize: 15, textShadow: "0 1px 3px rgba(0,0,0,0.5)" }}>
        {props.title}
      </Typography.Text>
      {props.onSkip ? (
        <>
          <span style={{ display: "inline-block", width: 10 }} />
          <Button type="link" size="small" onClick={() => props.onSkip?.()} style={{ color: "#91d5ff", padding: 0, height: "auto" }}>
            跳过指引
          </Button>
        </>
      ) : null}
    </div>
  );
}

/**
 * 四块挡板形成矩形洞；洞区域无 DOM，点击穿透到下层（下层需为可交互元素）
 */
export function OnboardingSpotlight(props: {
  visible: boolean;
  hole: SpotRect | null;
  title: string;
  zBase?: number;
  fallbackFullWhenHoleMissing?: boolean;
  onSkip?: () => void;
}) {
  const { visible, hole, title, zBase = 10058, fallbackFullWhenHoleMissing = true, onSkip } = props;

  if (!visible) return null;

  const dim = "rgba(0, 0, 0, 0.58)";
  const zHint = zBase + 5;

  if (!hole) {
    if (!fallbackFullWhenHoleMissing) return null;
    return (
      <>
        <div
          role="presentation"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: zBase,
            background: dim,
            pointerEvents: "auto",
          }}
        />
        <OnboardingHintBubble title={title} zForeground={zHint} style={fallbackBottomHintStyle} onSkip={onSkip} />
      </>
    );
  }

  const { top, left, width, height } = hole;
  const topH = Math.max(0, top);
  const leftW = Math.max(0, left);
  const bottomY = top + height;
  const rightX = left + width;
  const midH = Math.max(0, bottomY - topH);

  const hintStyle = hintStyleAdjacentToHole(hole);

  return (
    <>
      <div style={{ position: "fixed", inset: 0, zIndex: zBase, pointerEvents: "none" }}>
        <div
          role="presentation"
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: topH,
            background: dim,
            pointerEvents: "auto",
          }}
        />
        <div
          role="presentation"
          style={{
            position: "absolute",
            top: bottomY,
            left: 0,
            right: 0,
            bottom: 0,
            background: dim,
            pointerEvents: "auto",
          }}
        />
        <div
          role="presentation"
          style={{
            position: "absolute",
            top: topH,
            left: 0,
            width: leftW,
            height: midH,
            background: dim,
            pointerEvents: "auto",
          }}
        />
        <div
          role="presentation"
          style={{
            position: "absolute",
            top: topH,
            left: rightX,
            right: 0,
            height: midH,
            background: dim,
            pointerEvents: "auto",
          }}
        />
      </div>
      <OnboardingHintBubble title={title} zForeground={zHint} style={hintStyle} onSkip={onSkip} />
    </>
  );
}
