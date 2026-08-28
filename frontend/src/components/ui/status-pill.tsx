// Адаптировано с 21st.dev (@diceui/status) под текущий стек проекта:
// import Slot из уже используемого в проекте @radix-ui/react-slot вместо
// мета-пакета radix-ui, чтобы не тянуть новую зависимость.
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";
import { cn } from "@/lib/utils";

const statusPillVariants = cva(
  "inline-flex w-fit shrink-0 items-center gap-1.5 overflow-hidden whitespace-nowrap rounded-full border px-2.5 py-1 font-medium text-xs transition-colors",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-muted text-muted-foreground [&_[data-slot=status-pill-indicator]]:bg-muted-foreground",
        success:
          "border-positive/20 bg-positive/10 text-positive [&_[data-slot=status-pill-indicator]]:bg-positive",
        error:
          "border-danger/20 bg-danger/10 text-danger [&_[data-slot=status-pill-indicator]]:bg-danger",
        warning:
          "border-session1/20 bg-session1/10 text-session1 [&_[data-slot=status-pill-indicator]]:bg-session1",
        info:
          "border-session2/20 bg-session2/10 text-session2 [&_[data-slot=status-pill-indicator]]:bg-session2",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

interface StatusPillProps
  extends VariantProps<typeof statusPillVariants>,
    React.ComponentProps<"div"> {
  asChild?: boolean;
}

function StatusPill(props: StatusPillProps) {
  const { className, variant = "default", asChild, ...rootProps } = props;
  const RootPrimitive = asChild ? Slot : "div";

  return (
    <RootPrimitive
      data-slot="status-pill"
      data-variant={variant}
      {...rootProps}
      className={cn(statusPillVariants({ variant }), className)}
    />
  );
}

function StatusPillIndicator(props: React.ComponentProps<"div">) {
  const { className, ...indicatorProps } = props;
  return (
    <div
      data-slot="status-pill-indicator"
      {...indicatorProps}
      className={cn(
        "relative flex size-2 shrink-0 rounded-full",
        "before:absolute before:inset-0 before:animate-ping before:rounded-full before:bg-inherit",
        "after:absolute after:inset-[2px] after:rounded-full after:bg-inherit",
        className,
      )}
    />
  );
}

function StatusPillLabel(props: React.ComponentProps<"div">) {
  const { className, ...labelProps } = props;
  return (
    <div
      data-slot="status-pill-label"
      {...labelProps}
      className={cn("leading-none", className)}
    />
  );
}

export { StatusPill, StatusPillIndicator, StatusPillLabel, statusPillVariants };
