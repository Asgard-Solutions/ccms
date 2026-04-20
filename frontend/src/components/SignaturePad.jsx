import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "../components/ui/button";
import { Eraser } from "lucide-react";

/**
 * SignaturePad — minimal canvas-based signature capture. Pure React, zero
 * external dependencies. Emits `onChange(dataUrl | null)` whenever the user
 * finishes a stroke (or clears the pad). `value` is a PNG data-URL.
 *
 * Supports mouse + touch (via Pointer Events). Honours devicePixelRatio so
 * the stored image is crisp on high-DPI displays while the canvas stays at
 * CSS-pixel dimensions.
 */
export function SignaturePad({ value, onChange, testId = "signature-pad", height = 140, disabled = false }) {
  const canvasRef = useRef(null);
  const ctxRef = useRef(null);
  const drawingRef = useRef(false);
  const lastPtRef = useRef(null);
  const [hasContent, setHasContent] = useState(Boolean(value));

  const init = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(rect.width * ratio);
    canvas.height = Math.floor(rect.height * ratio);
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = 1.8;
    ctx.strokeStyle = "#1F2924";
    ctxRef.current = ctx;
    // If a `value` was passed in (edit-mode) paint it into the canvas.
    if (value) {
      const img = new Image();
      img.onload = () => {
        ctx.drawImage(img, 0, 0, rect.width, rect.height);
        setHasContent(true);
      };
      img.src = value;
    }
  }, [value]);

  useEffect(() => {
    init();
    const onResize = () => init();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const posFrom = (evt) => {
    const rect = canvasRef.current.getBoundingClientRect();
    return { x: evt.clientX - rect.left, y: evt.clientY - rect.top };
  };

  const onDown = (e) => {
    if (disabled) return;
    e.preventDefault();
    canvasRef.current.setPointerCapture?.(e.pointerId);
    drawingRef.current = true;
    lastPtRef.current = posFrom(e);
  };
  const onMove = (e) => {
    if (!drawingRef.current) return;
    e.preventDefault();
    const ctx = ctxRef.current;
    const pt = posFrom(e);
    ctx.beginPath();
    ctx.moveTo(lastPtRef.current.x, lastPtRef.current.y);
    ctx.lineTo(pt.x, pt.y);
    ctx.stroke();
    lastPtRef.current = pt;
  };
  const onUp = () => {
    if (!drawingRef.current) return;
    drawingRef.current = false;
    setHasContent(true);
    onChange?.(canvasRef.current.toDataURL("image/png"));
  };

  const clear = () => {
    const canvas = canvasRef.current;
    const ctx = ctxRef.current;
    if (!canvas || !ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasContent(false);
    onChange?.(null);
  };

  return (
    <div data-testid={testId} className="space-y-2">
      <div className="relative overflow-hidden rounded-sm border border-stone-300 bg-white">
        <canvas
          ref={canvasRef}
          data-testid={`${testId}-canvas`}
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerLeave={onUp}
          style={{ width: "100%", height, touchAction: "none", cursor: disabled ? "not-allowed" : "crosshair" }}
        />
        {!hasContent && (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-[#A3AFA7]"
          >
            Sign above — draw with mouse or finger
          </div>
        )}
      </div>
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-[#5C6A61]">
          Your signature is stored as a PNG and attached to the matching consents.
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={clear}
          disabled={disabled || !hasContent}
          data-testid={`${testId}-clear`}
          className="h-7 text-xs text-[#5C6A61] hover:bg-[#EDF2EE]"
        >
          <Eraser className="mr-1 h-3 w-3" /> Clear
        </Button>
      </div>
    </div>
  );
}
