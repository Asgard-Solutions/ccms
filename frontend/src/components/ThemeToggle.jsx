import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "../contexts/ThemeContext";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { Button } from "./ui/button";

/**
 * ThemeToggle — compact dropdown offering Light / Dark / System.
 * Persists the chosen mode for the current user via ThemeContext.
 */
export default function ThemeToggle() {
  const { mode, effective, setTheme } = useTheme();
  const Icon = effective === "dark" ? Moon : Sun;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          data-testid="theme-toggle"
          variant="ghost"
          size="icon"
          aria-label="Change theme"
          className="h-9 w-9 text-[color:var(--text-muted)] hover:text-[color:var(--text-strong)]"
        >
          <Icon className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuLabel className="text-xs uppercase tracking-[0.12em] text-[color:var(--text-muted)]">
          Theme
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          data-testid="theme-option-light"
          onClick={() => setTheme("light")}
          className={mode === "light" ? "font-semibold" : ""}
        >
          <Sun className="mr-2 h-4 w-4" /> Light
        </DropdownMenuItem>
        <DropdownMenuItem
          data-testid="theme-option-dark"
          onClick={() => setTheme("dark")}
          className={mode === "dark" ? "font-semibold" : ""}
        >
          <Moon className="mr-2 h-4 w-4" /> Dark
        </DropdownMenuItem>
        <DropdownMenuItem
          data-testid="theme-option-system"
          onClick={() => setTheme("system")}
          className={mode === "system" ? "font-semibold" : ""}
        >
          <Monitor className="mr-2 h-4 w-4" /> System
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
