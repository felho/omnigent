// Post-setup "Your imports are ready" modal: reviews the credentials, MCP
// servers, and skills Omnigent found in the user's existing harnesses. The
// credentials are already adopted (read-only); MCPs and skills are opt-in
// checkboxes, all selected by default, applied on Confirm.

import { useState, type ReactNode } from "react";
// Colored (brand) harness glyphs — the Color subpath keeps antd out of the
// bundle. Cursor has no Color variant, so it uses Mono.
import ClaudeCodeColor from "@lobehub/icons/es/ClaudeCode/components/Color";
import CodexColor from "@lobehub/icons/es/Codex/components/Color";
import CursorMono from "@lobehub/icons/es/Cursor/components/Mono";
import { ArrowRight, Check, XIcon } from "lucide-react";
import omnigentLogo from "@/assets/omnigent-starfish-icon.png";
import BlobGraphic from "@/components/onboarding/BlobGraphic";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export type ImportHarness = "claude-code" | "codex" | "cursor";

export interface ImportCredential {
  harness: ImportHarness;
  /** Where the harness's login comes from, e.g. "Databricks AI Gateway". */
  source: string;
}

export interface ImportMcpServer {
  id: string;
  name: string;
  harness: ImportHarness;
  toolCount?: number;
}

export interface ImportSkill {
  id: string;
  name: string;
  harness: ImportHarness;
}

export interface ImportContext {
  credentials: ImportCredential[];
  mcps: ImportMcpServer[];
  skills: ImportSkill[];
}

/** Ids of the MCP servers and skills left checked when the user confirmed. */
export interface ImportSelection {
  mcps: string[];
  skills: string[];
}

const HARNESSES: Record<ImportHarness, { label: string; icon: (size: number) => ReactNode }> = {
  "claude-code": { label: "Claude Code", icon: (size) => <ClaudeCodeColor size={size} /> },
  codex: { label: "Codex", icon: (size) => <CodexColor size={size} /> },
  cursor: { label: "Cursor", icon: (size) => <CursorMono size={size} /> },
};

const BAND_HARNESSES: ImportHarness[] = ["claude-code", "codex", "cursor"];

function BandTile({ children, zIndex }: { children: ReactNode; zIndex?: number }) {
  return (
    <span
      className="relative flex size-12 items-center justify-center rounded-xl border border-border bg-background"
      style={{ zIndex }}
    >
      {children}
    </span>
  );
}

/** Harness icons → Omnigent starfish, over the onboarding blob graphic. */
function ImportBand() {
  return (
    <div className="relative h-[200px] shrink-0 overflow-hidden">
      <BlobGraphic />
      <div className="absolute inset-0 flex items-center justify-center gap-5" aria-hidden="true">
        <div className="flex -space-x-1">
          {BAND_HARNESSES.map((harness, index) => (
            <BandTile key={harness} zIndex={index + 1}>
              {HARNESSES[harness].icon(32)}
            </BandTile>
          ))}
        </div>
        <ArrowRight className="size-4 text-muted-foreground" />
        <BandTile>
          <img src={omnigentLogo} alt="" className="size-8 object-contain" />
        </BandTile>
      </div>
    </div>
  );
}

function EmptyTab({ label }: { label: string }) {
  return <p className="py-6 text-center text-xs text-muted-foreground">No {label} detected</p>;
}

function CredentialRows({ credentials }: { credentials: ImportCredential[] }) {
  if (credentials.length === 0) return <EmptyTab label="credentials" />;
  return (
    <ul>
      {credentials.map(({ harness, source }) => (
        <li
          key={harness}
          className="flex items-center gap-3 border-b border-border py-3 last:border-b-0"
        >
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
            {HARNESSES[harness].icon(16)}
          </span>
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="truncate text-ui font-medium text-foreground">
              {HARNESSES[harness].label}
            </span>
            <span className="truncate text-xs text-muted-foreground">{source}</span>
          </span>
          <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
            <Check className="size-3.5 text-success" aria-hidden="true" />
            Imported
          </span>
        </li>
      ))}
    </ul>
  );
}

interface SelectableRow {
  id: string;
  name: string;
  metadata: string;
}

function SelectableRows({
  kind,
  rows,
  selected,
  onToggle,
}: {
  kind: string;
  rows: SelectableRow[];
  selected: ReadonlySet<string>;
  onToggle: (id: string, checked: boolean) => void;
}) {
  if (rows.length === 0) return <EmptyTab label={kind} />;
  return (
    <ul>
      {rows.map(({ id, name, metadata }) => {
        const inputId = `import-${kind}-${id}`.replace(/[^\w-]/g, "-");
        return (
          <li
            key={id}
            className="flex items-center gap-3 border-b border-border py-2 last:border-b-0"
          >
            <Checkbox
              id={inputId}
              checked={selected.has(id)}
              onCheckedChange={(checked) => onToggle(id, checked === true)}
            />
            <label
              htmlFor={inputId}
              className="min-w-0 flex-1 cursor-pointer truncate text-ui font-medium text-foreground"
            >
              {name}
            </label>
            <span className="shrink-0 text-xs text-muted-foreground">{metadata}</span>
          </li>
        );
      })}
    </ul>
  );
}

function mcpMetadata({ harness, toolCount }: ImportMcpServer): string {
  const label = HARNESSES[harness].label;
  if (toolCount == null) return label;
  return `${toolCount} ${toolCount === 1 ? "tool" : "tools"} · ${label}`;
}

function toggle(set: ReadonlySet<string>, id: string, checked: boolean): Set<string> {
  const next = new Set(set);
  if (checked) next.add(id);
  else next.delete(id);
  return next;
}

export interface ImportContextModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  context: ImportContext;
  onConfirm: (selection: ImportSelection) => void;
}

export function ImportContextModal({
  open,
  onOpenChange,
  context,
  onConfirm,
}: ImportContextModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="flex h-[640px] flex-col gap-0 overflow-hidden rounded-[20px] p-0 sm:max-w-[560px]"
      >
        {/* Content unmounts on close, so the selection resets to all-checked
            each time the modal reopens. */}
        <ImportContextBody
          context={context}
          onConfirm={(selection) => {
            onConfirm(selection);
            onOpenChange(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

function ImportContextBody({
  context,
  onConfirm,
}: Pick<ImportContextModalProps, "context" | "onConfirm">) {
  const [selectedMcps, setSelectedMcps] = useState<ReadonlySet<string>>(
    () => new Set(context.mcps.map((mcp) => mcp.id)),
  );
  const [selectedSkills, setSelectedSkills] = useState<ReadonlySet<string>>(
    () => new Set(context.skills.map((skill) => skill.id)),
  );

  const confirm = () => {
    onConfirm({
      mcps: context.mcps.filter((mcp) => selectedMcps.has(mcp.id)).map((mcp) => mcp.id),
      skills: context.skills.filter((s) => selectedSkills.has(s.id)).map((s) => s.id),
    });
  };

  return (
    <>
      <ImportBand />
      <DialogClose asChild>
        <Button variant="ghost" size="icon-sm" className="absolute top-3 right-3 z-10">
          <XIcon className="size-4 text-foreground/70" />
          <span className="sr-only">Close</span>
        </Button>
      </DialogClose>

      <div className="flex min-h-0 flex-1 flex-col px-5 pt-5">
        <div className="flex flex-col items-center gap-1 py-2 text-center">
          <DialogTitle className="min-h-0 pr-0 text-2xl leading-8 font-normal tracking-[-0.02em]">
            Your imports are ready
          </DialogTitle>
          <DialogDescription className="max-w-[480px] text-[14px] leading-5">
            Review what Omnigent brought over from your harnesses.
          </DialogDescription>
        </div>

        <Tabs defaultValue="credentials" className="mt-5 min-h-0 flex-1 gap-0">
          <TabsList
            variant="line"
            className="h-9 w-full justify-start gap-4 rounded-none border-b border-border p-0"
          >
            <TabsTrigger value="credentials" className="flex-none px-0">
              Credentials
            </TabsTrigger>
            <TabsTrigger value="mcps" className="flex-none px-0">
              MCPs
            </TabsTrigger>
            <TabsTrigger value="skills" className="flex-none px-0">
              Skills
            </TabsTrigger>
          </TabsList>
          <div className="min-h-0 flex-1 overflow-y-auto pt-2">
            <TabsContent value="credentials">
              <CredentialRows credentials={context.credentials} />
            </TabsContent>
            <TabsContent value="mcps">
              <SelectableRows
                kind="MCPs"
                rows={context.mcps.map((mcp) => ({ ...mcp, metadata: mcpMetadata(mcp) }))}
                selected={selectedMcps}
                onToggle={(id, checked) => setSelectedMcps((s) => toggle(s, id, checked))}
              />
            </TabsContent>
            <TabsContent value="skills">
              <SelectableRows
                kind="skills"
                rows={context.skills.map((skill) => ({
                  id: skill.id,
                  name: `$${skill.name}`,
                  metadata: HARNESSES[skill.harness].label,
                }))}
                selected={selectedSkills}
                onToggle={(id, checked) => setSelectedSkills((s) => toggle(s, id, checked))}
              />
            </TabsContent>
          </div>
        </Tabs>
      </div>

      <div className="flex shrink-0 justify-end px-5 pt-4 pb-5">
        <Button onClick={confirm}>Confirm</Button>
      </div>
    </>
  );
}

export default ImportContextModal;
