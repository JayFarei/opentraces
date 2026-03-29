import { forwardRef, useImperativeHandle, useCallback } from "react";
import type { AnimatedIconHandle, AnimatedIconProps } from "./types";
import { motion, useAnimate } from "motion/react";

const StarIcon = forwardRef<AnimatedIconHandle, AnimatedIconProps>(
  (
    { size = 24, color = "currentColor", strokeWidth = 2, className = "" },
    ref,
  ) => {
    const [scope, animate] = useAnimate();

    const start = useCallback(async () => {
      animate(
        ".star-fill",
        { opacity: [0, 1], scale: [0.8, 1] },
        { duration: 0.4, ease: "easeOut" },
      );
      animate(
        ".star-outline",
        { scale: [1, 1.1, 1], rotate: [0, -5, 5, 0] },
        { duration: 0.5, ease: "easeInOut" },
      );
    }, [animate]);

    const stop = useCallback(() => {
      animate(
        ".star-fill",
        { opacity: [1, 0] },
        { duration: 0.3, ease: "easeOut" },
      );
      animate(
        ".star-outline",
        { scale: 1, rotate: 0 },
        { duration: 0.3, ease: "easeInOut" },
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
      >
        <path stroke="none" d="M0 0h24v24H0z" fill="none" />
        <motion.path
          className="star-fill"
          d="M12 17.75l-6.172 3.245l1.179 -6.873l-5 -4.867l6.9 -1l3.086 -6.253l3.086 6.253l6.9 1l-5 4.867l1.179 6.873z"
          fill={color}
          opacity={0}
          style={{ transformOrigin: "center" }}
        />
        <motion.path
          className="star-outline"
          d="M12 17.75l-6.172 3.245l1.179 -6.873l-5 -4.867l6.9 -1l3.086 -6.253l3.086 6.253l6.9 1l-5 4.867l1.179 6.873z"
          style={{ transformOrigin: "center" }}
        />
      </motion.svg>
    );
  },
);

StarIcon.displayName = "StarIcon";
export default StarIcon;
