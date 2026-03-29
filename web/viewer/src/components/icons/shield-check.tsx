import { forwardRef, useImperativeHandle, useCallback } from "react";
import type { AnimatedIconHandle, AnimatedIconProps } from "./types";
import { motion, useAnimate } from "motion/react";

const ShieldCheck = forwardRef<AnimatedIconHandle, AnimatedIconProps>(
  (
    { size = 24, color = "currentColor", strokeWidth = 2, className = "" },
    ref,
  ) => {
    const [scope, animate] = useAnimate();

    const start = useCallback(async () => {
      animate(
        ".shield-body",
        { scale: [1, 1.05, 1] },
        { duration: 0.35, ease: "easeOut" },
      );

      await animate(
        ".shield-check",
        { pathLength: [0, 1], opacity: [0, 1] },
        { duration: 0.3, ease: "easeInOut" },
      );
    }, [animate]);

    const stop = useCallback(() => {
      animate(".shield-body", { scale: 1 }, { duration: 0.2 });
      animate(
        ".shield-check",
        { pathLength: 1, opacity: 1 },
        { duration: 0.2 },
      );
    }, [animate]);

    useImperativeHandle(ref, () => ({
      startAnimation: start,
      stopAnimation: stop,
    }));

    return (
      <motion.svg
        ref={scope}
        onHoverStart={start}
        onHoverEnd={stop}
        xmlns="http://www.w3.org/2000/svg"
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={`cursor-pointer ${className}`}
        style={{ overflow: "visible" }}
      >
        <motion.path
          className="shield-body"
          style={{ transformOrigin: "50% 50%" }}
          d="M11.46 20.846a12 12 0 0 1 -7.96 -14.846a12 12 0 0 0 8.5 -3a12 12 0 0 0 8.5 3a12 12 0 0 1 -.09 7.06"
        />

        <motion.path
          className="shield-check"
          d="M15 19l2 2l4 -4"
          initial={{ pathLength: 1, opacity: 1 }}
        />
      </motion.svg>
    );
  },
);

ShieldCheck.displayName = "ShieldCheck";
export default ShieldCheck;
