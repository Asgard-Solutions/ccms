import { useState } from "react";
import {
  CheckCircle2,
  AlertTriangle,
  Info,
  XCircle,
  Palette,
  Type,
  Square,
  Layout as LayoutIcon,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "../components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "../components/ui/table";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "../components/ui/select";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "../components/ui/dropdown-menu";
import { toast } from "../components/ui/sonner";
import { useTheme } from "../contexts/ThemeContext";

// -----------------------------------------------------------------------
// Reusable layout primitives (only used on this page, so left inline).
// -----------------------------------------------------------------------
const Section = ({ icon: Icon, title, description, children, testId }) => (
  <section data-testid={testId} className="space-y-4">
    <header className="flex items-start gap-3">
      <span className="flex h-10 w-10 items-center justify-center rounded-sm bg-primary/10 text-primary">
        <Icon className="h-5 w-5" />
      </span>
      <div>
        <h2 className="font-display text-xl font-semibold tracking-tight">{title}</h2>
        {description ? (
          <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
    </header>
    <div className="rounded-lg border border-border bg-card p-5 shadow-xs">
      {children}
    </div>
  </section>
);

const Swatch = ({ name, cssVar, textVar }) => (
  <div
    data-testid={`swatch-${name}`}
    className="flex items-center gap-3 rounded-sm border border-border p-3"
  >
    <span
      className="h-10 w-10 shrink-0 rounded-sm border border-border"
      style={{ background: `hsl(var(--${cssVar}))` }}
    />
    <div className="min-w-0">
      <div className="font-mono text-[12px] text-foreground">{name}</div>
      <div className="font-mono text-[11px] text-muted-foreground">{textVar}</div>
    </div>
  </div>
);

const DirectSwatch = ({ name, cssVar }) => (
  <div
    data-testid={`swatch-${name}`}
    className="flex items-center gap-3 rounded-sm border border-border p-3"
  >
    <span
      className="h-10 w-10 shrink-0 rounded-sm border border-border"
      style={{ background: `var(--${cssVar})` }}
    />
    <div className="min-w-0">
      <div className="font-mono text-[12px] text-foreground">{name}</div>
      <div className="font-mono text-[11px] text-muted-foreground">var(--{cssVar})</div>
    </div>
  </div>
);

// -----------------------------------------------------------------------
// Theme preview page — one-screen regression canary (spec §19).
// Exposes every shadcn primitive in its default, hover, focus, disabled,
// and error states alongside the semantic token palette. Light + dark
// parity can be confirmed visually from a single URL.
// -----------------------------------------------------------------------
export default function ThemePreview() {
  const { mode, setTheme, effective } = useTheme();
  const [inputError, setInputError] = useState(false);

  return (
    <div data-testid="theme-preview-page" className="mx-auto max-w-6xl space-y-10">
      {/* ---------------------------- Header ---------------------------- */}
      <header className="flex flex-col gap-2 border-b border-border pb-6">
        <span className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Design system
        </span>
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-4xl font-semibold tracking-tight text-foreground">
              Theme preview
            </h1>
            <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
              A one-screen regression canary for the Chiro Software theme system.
              Every primitive is rendered in its default, hover, focus, disabled,
              and error states so light · dark · system parity can be confirmed
              from a single URL.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {["light", "dark", "system"].map((k) => (
              <Button
                key={k}
                data-testid={`theme-set-${k}`}
                variant={mode === k ? "default" : "outline"}
                size="sm"
                onClick={() => setTheme(k)}
              >
                {k}
              </Button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap gap-4 pt-2 text-xs text-muted-foreground">
          <span>Preference: <span className="font-mono text-foreground">{mode}</span></span>
          <span>Effective: <span className="font-mono text-foreground">{effective}</span></span>
          <span>Source: <code className="font-mono text-foreground">docs/theme/</code></span>
        </div>
      </header>

      {/* ---------------------------- Palette ---------------------------- */}
      <Section
        testId="section-palette"
        icon={Palette}
        title="Semantic tokens"
        description="Feature code should consume only these. No raw hex, no palette classes."
      >
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Swatch name="background" cssVar="background" textVar="--background" />
          <Swatch name="foreground" cssVar="foreground" textVar="--foreground" />
          <Swatch name="card" cssVar="card" textVar="--card" />
          <Swatch name="popover" cssVar="popover" textVar="--popover" />
          <Swatch name="primary" cssVar="primary" textVar="--primary" />
          <Swatch name="secondary" cssVar="secondary" textVar="--secondary" />
          <Swatch name="muted" cssVar="muted" textVar="--muted" />
          <Swatch name="accent" cssVar="accent" textVar="--accent" />
          <Swatch name="border" cssVar="border" textVar="--border" />
          <Swatch name="input" cssVar="input" textVar="--input" />
          <Swatch name="ring" cssVar="ring" textVar="--ring" />
          <Swatch name="destructive" cssVar="destructive" textVar="--destructive" />
          <DirectSwatch name="surface" cssVar="surface" />
          <DirectSwatch name="surface-2" cssVar="surface-2" />
          <DirectSwatch name="surface-3" cssVar="surface-3" />
          <DirectSwatch name="focus" cssVar="focus" />
          <DirectSwatch name="success" cssVar="success" />
          <DirectSwatch name="warning" cssVar="warning" />
          <DirectSwatch name="info" cssVar="info" />
          <DirectSwatch name="accent-strong" cssVar="accent-strong" />
        </div>
      </Section>

      {/* ---------------------------- Typography ---------------------------- */}
      <Section
        testId="section-typography"
        icon={Type}
        title="Typography"
        description="Outfit for display, Manrope for body, JetBrains Mono for technical values."
      >
        <div className="space-y-3">
          <div className="font-display text-5xl font-bold tracking-tight text-foreground">
            Display / 48 · Outfit 700
          </div>
          <div className="font-display text-3xl font-semibold tracking-tight text-foreground">
            H1 / 30 · Outfit 650
          </div>
          <div className="font-display text-xl font-semibold text-foreground">
            H2 / 20 · Outfit 650
          </div>
          <div className="text-base text-foreground">
            Body L / 16 · Manrope 500 — Operational copy that the team reads for
            hours at a time. Stays comfortable under dense workflows.
          </div>
          <div className="text-sm text-muted-foreground">
            Body S / 14 · Manrope 500 muted — secondary/helper context.
          </div>
          <div className="font-mono text-xs text-foreground">
            Mono / 12 · JetBrains Mono 500 — patient-id · audit-ref · 2026-04-20T15:48:11Z
          </div>
        </div>
      </Section>

      {/* ---------------------------- Buttons ---------------------------- */}
      <Section
        testId="section-buttons"
        icon={Square}
        title="Buttons"
        description="Six variants × four sizes. Default 40px · rounded-sm · semibold · accessible focus ring."
      >
        <div className="flex flex-wrap gap-3">
          <Button data-testid="btn-default">Primary</Button>
          <Button data-testid="btn-secondary" variant="secondary">Secondary</Button>
          <Button data-testid="btn-outline" variant="outline">Outline</Button>
          <Button data-testid="btn-ghost" variant="ghost">Ghost</Button>
          <Button data-testid="btn-link" variant="link">Link</Button>
          <Button data-testid="btn-destructive" variant="destructive">Destructive</Button>
          <Button data-testid="btn-disabled" disabled>Disabled</Button>
        </div>
        <div className="mt-4 flex flex-wrap gap-3">
          <Button size="sm">Small</Button>
          <Button>Default 40px</Button>
          <Button size="lg">Large</Button>
          <Button size="icon" aria-label="Icon">
            <CheckCircle2 className="h-4 w-4" />
          </Button>
        </div>
      </Section>

      {/* ---------------------------- Inputs ---------------------------- */}
      <Section
        testId="section-inputs"
        icon={LayoutIcon}
        title="Inputs & Select"
        description="40px height · rounded-sm · visible border in both themes · 2-ring focus state."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Default
            </label>
            <Input data-testid="input-default" placeholder="e.g. Jane Doe" />
          </div>
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              With value
            </label>
            <Input data-testid="input-value" defaultValue="Morgan Lee" />
          </div>
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Disabled
            </label>
            <Input data-testid="input-disabled" disabled defaultValue="Read-only" />
          </div>
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Error
            </label>
            <Input
              data-testid="input-error"
              defaultValue=""
              placeholder="e.g. (555) 123-4567"
              aria-invalid={inputError}
              className={inputError ? "border-destructive focus-visible:ring-destructive" : ""}
              onFocus={() => setInputError(true)}
              onBlur={() => setInputError(false)}
            />
            {inputError ? (
              <p className="text-xs text-destructive">This field is required.</p>
            ) : (
              <p className="text-xs text-muted-foreground">Focus to preview error state.</p>
            )}
          </div>
          <div className="space-y-2 md:col-span-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Textarea
            </label>
            <Textarea
              data-testid="textarea-default"
              rows={3}
              placeholder="Free-form clinical notes…"
            />
          </div>
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Select
            </label>
            <Select>
              <SelectTrigger data-testid="select-trigger">
                <SelectValue placeholder="Select a provider" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="monroe">Dr. Alicia Monroe</SelectItem>
                <SelectItem value="varma">Dr. Neel Varma</SelectItem>
                <SelectItem value="howe">Dr. Sam Howe</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Dropdown menu
            </label>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button data-testid="dropdown-trigger" variant="outline">
                  Actions
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuLabel>Patient actions</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem>View profile</DropdownMenuItem>
                <DropdownMenuItem>Schedule visit</DropdownMenuItem>
                <DropdownMenuItem>Export chart</DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem className="text-destructive focus:text-destructive">
                  Archive
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </Section>

      {/* ---------------------------- Badges ---------------------------- */}
      <Section
        testId="section-badges"
        icon={Palette}
        title="Badges"
        description="Semantic variants for status. Copper premium variant reserved for billing / admin emphasis (spec §9)."
      >
        <div className="flex flex-wrap gap-2">
          <Badge data-testid="badge-default">Default</Badge>
          <Badge data-testid="badge-secondary" variant="secondary">Secondary</Badge>
          <Badge data-testid="badge-outline" variant="outline">Outline</Badge>
          <Badge data-testid="badge-success" variant="success">Active</Badge>
          <Badge data-testid="badge-warning" variant="warning">Review</Badge>
          <Badge data-testid="badge-info" variant="info">Info</Badge>
          <Badge data-testid="badge-destructive" variant="destructive">Cancelled</Badge>
          <Badge data-testid="badge-premium" variant="premium">Premium</Badge>
        </div>
      </Section>

      {/* ---------------------------- Tabs ---------------------------- */}
      <Section
        testId="section-tabs"
        icon={LayoutIcon}
        title="Tabs"
        description="Compact, bordered container. Active trigger uses bg-card + shadow-xs, not pill-shaped."
      >
        <Tabs defaultValue="overview">
          <TabsList data-testid="tabs-list">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="clinical">Clinical</TabsTrigger>
            <TabsTrigger value="billing">Billing</TabsTrigger>
            <TabsTrigger value="documents">Documents</TabsTrigger>
          </TabsList>
          <TabsContent value="overview" className="text-sm text-muted-foreground">
            Overview tab content.
          </TabsContent>
          <TabsContent value="clinical" className="text-sm text-muted-foreground">
            Clinical records tab content.
          </TabsContent>
          <TabsContent value="billing" className="text-sm text-muted-foreground">
            Billing tab content.
          </TabsContent>
          <TabsContent value="documents" className="text-sm text-muted-foreground">
            Documents tab content.
          </TabsContent>
        </Tabs>
      </Section>

      {/* ---------------------------- Card ---------------------------- */}
      <Section
        testId="section-cards"
        icon={Square}
        title="Cards"
        description="12px radius · border-first · shadow-xs · 20px padding (p-5)."
      >
        <div className="grid gap-4 md:grid-cols-3">
          <Card data-testid="card-kpi">
            <CardHeader>
              <CardDescription>Today&apos;s appointments</CardDescription>
              <CardTitle className="text-3xl">12</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground">
                <span className="text-success">+2</span> vs. yesterday
              </p>
            </CardContent>
          </Card>
          <Card data-testid="card-outstanding">
            <CardHeader>
              <CardDescription>Outstanding invoices</CardDescription>
              <CardTitle className="text-3xl tabular-nums">$1,248.00</CardTitle>
            </CardHeader>
            <CardContent>
              <Badge variant="warning">3 past due</Badge>
            </CardContent>
            <CardFooter>
              <Button variant="link" className="px-0">
                Review billing →
              </Button>
            </CardFooter>
          </Card>
          <Card data-testid="card-premium">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardDescription>Concierge plan</CardDescription>
                <Badge variant="premium">Premium</Badge>
              </div>
              <CardTitle className="text-3xl">Gold</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground">
                Extended hours · home visits · expedited billing
              </p>
            </CardContent>
          </Card>
        </div>
      </Section>

      {/* ---------------------------- Table ---------------------------- */}
      <Section
        testId="section-table"
        icon={LayoutIcon}
        title="Table"
        description="12px bold uppercase headers · tabular-nums cells · tokenized hover + selected rows."
      >
        <div className="overflow-hidden rounded-lg border border-border">
          <Table data-testid="preview-table">
            <TableHeader>
              <TableRow>
                <TableHead>Patient</TableHead>
                <TableHead>Provider</TableHead>
                <TableHead>Amount</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <TableCell className="font-medium">Morgan Lee</TableCell>
                <TableCell>Dr. Monroe</TableCell>
                <TableCell className="text-right">$120.00</TableCell>
                <TableCell><Badge variant="success">Paid</Badge></TableCell>
              </TableRow>
              <TableRow data-state="selected">
                <TableCell className="font-medium">Jane Cole</TableCell>
                <TableCell>Dr. Varma</TableCell>
                <TableCell className="text-right">$240.00</TableCell>
                <TableCell><Badge variant="warning">Review</Badge></TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Sam Howe</TableCell>
                <TableCell>Dr. Monroe</TableCell>
                <TableCell className="text-right">$90.00</TableCell>
                <TableCell><Badge variant="destructive">Cancelled</Badge></TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Alex Patel</TableCell>
                <TableCell>Dr. Howe</TableCell>
                <TableCell className="text-right">$310.00</TableCell>
                <TableCell><Badge variant="info">Scheduled</Badge></TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </Section>

      {/* ---------------------------- Dialog + Toasts ---------------------------- */}
      <Section
        testId="section-dialog-toasts"
        icon={AlertTriangle}
        title="Dialog & Toasts"
        description="Dialog: 12px radius · tokenized overlay · bg-card surface. Toasts: semantic state classes via the shared ThemeContext."
      >
        <div className="flex flex-wrap gap-3">
          <Dialog>
            <DialogTrigger asChild>
              <Button data-testid="open-dialog">Open dialog</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Archive patient record?</DialogTitle>
                <DialogDescription>
                  Archived records are retained for 7 years per HIPAA
                  technical safeguards but removed from daily search.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="outline">Cancel</Button>
                <Button variant="destructive">Archive</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <Button
            data-testid="toast-success"
            variant="outline"
            onClick={() => toast.success("Appointment confirmed for 3:00 PM.")}
          >
            <CheckCircle2 className="h-4 w-4" /> Success toast
          </Button>
          <Button
            data-testid="toast-warning"
            variant="outline"
            onClick={() => toast.warning("Patient chart requires review.")}
          >
            <AlertTriangle className="h-4 w-4" /> Warning toast
          </Button>
          <Button
            data-testid="toast-info"
            variant="outline"
            onClick={() => toast.info("New release available.")}
          >
            <Info className="h-4 w-4" /> Info toast
          </Button>
          <Button
            data-testid="toast-error"
            variant="outline"
            onClick={() => toast.error("Failed to sync with EHR.")}
          >
            <XCircle className="h-4 w-4" /> Error toast
          </Button>
        </div>
      </Section>

      <footer className="border-t border-border pt-6 text-xs text-muted-foreground">
        See <code className="font-mono text-foreground">docs/theme/CHIRO_UI_REVIEW_AND_COMPLIANCE_CHECKLIST.md</code>
        {" "}for the pass/fail review rubric. Raw hex or Tailwind palette classes
        in feature code are blocked by <code className="font-mono text-foreground">scripts/check_theme.py</code>.
      </footer>
    </div>
  );
}
