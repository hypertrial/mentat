"""Pinned Lean 4.35 helper sources for the isolated proof checker.

Parser: SOURCE POLICY_JSON approved|submission; exporter: MODULE (NDJSON stdout).
Audit: APPROVED_EXPORT SOLUTION_EXPORT POLICY_JSON [approved]. The optional final
argument checks approved placeholders only. All verdicts are data-only JSON.
The exporter must run inside the isolated build boundary with the selected module
as its sole untrusted search path. The parser uses a trusted, sanitized search path.
"""

PARSER_SOURCE = r"""import Lean
open Lean

def getList (j : Json) (key : String) : IO (Array String) :=
  match j.getObjValAs? (Array String) key with
  | .ok v => pure v
  | .error _ => throw (IO.userError "invalid policy")

def output (s : String) : IO Unit := IO.println (Json.mkObj [("status", s), ("detail", if s == "accepted" then "source policy passed" else if s == "rejected" then "source policy rejected" else "source parser unavailable")]).compress
partial def hasTokens (s : Syntax) : Bool :=
  match s with
  | .atom .. | .ident .. => true
  | .node _ _ args => args.any hasTokens
  | .missing => false

-- This is an allowlist of parser node kinds used by the reviewed pilot fragment.
def allowedKinds : Array String := #[
  "null", "choice", "fieldIdx", "hygieneInfo", "num", "term!_", "termIfThenElse",
  "term[_]", "term_!=_", "term_&&_", "term_*_", "term_++_", "term_+_", "term_<_", "term_==_", "term_=_", "term_>_", "term_||_", "term_×_", "term_∧_", "term_≤_", "term_≥_", "term{}",
  "Lean.Parser.Command.ctor", "Lean.Parser.Command.declId", "Lean.Parser.Command.declModifiers", "Lean.Parser.Command.declSig", "Lean.Parser.Command.declValEqns", "Lean.Parser.Command.declValSimple", "Lean.Parser.Command.declaration", "Lean.Parser.Command.definition", "Lean.Parser.Command.derivingClass", "Lean.Parser.Command.inductive", "Lean.Parser.Command.optDeclSig", "Lean.Parser.Command.optDeriving", "Lean.Parser.Command.structFields", "Lean.Parser.Command.structSimpleBinder", "Lean.Parser.Command.structure", "Lean.Parser.Command.structureTk", "Lean.Parser.Command.theorem", "Lean.Parser.Termination.suffix",
  "Lean.Parser.Term.app", "Lean.Parser.Term.arrow", "Lean.Parser.Term.basicFun", "Lean.Parser.Term.binderDefault", "Lean.Parser.Term.depArrow", "Lean.Parser.Term.dotIdent", "Lean.Parser.Term.explicitBinder", "Lean.Parser.Term.forall", "Lean.Parser.Term.fun", "Lean.Parser.Term.hole", "Lean.Parser.Term.hygienicLParen", "Lean.Parser.Term.implicitBinder", "Lean.Parser.Term.let", "Lean.Parser.Term.letConfig", "Lean.Parser.Term.letDecl", "Lean.Parser.Term.letId", "Lean.Parser.Term.letIdDecl", "Lean.Parser.Term.match", "Lean.Parser.Term.matchAlt", "Lean.Parser.Term.matchAlts", "Lean.Parser.Term.matchAltsWhereDecls", "Lean.Parser.Term.matchDiscr", "Lean.Parser.Term.optEllipsis", "Lean.Parser.Term.paren", "Lean.Parser.Term.proj", "Lean.Parser.Term.prop", "Lean.Parser.Term.structInst", "Lean.Parser.Term.structInstField", "Lean.Parser.Term.structInstFieldDef", "Lean.Parser.Term.structInstFields", "Lean.Parser.Term.structInstLVal", "Lean.Parser.Term.tuple", "Lean.Parser.Term.typeAscription", "Lean.Parser.Term.typeSpec"]

partial def safeSyntax (s : Syntax) (pattern : Bool := false) : Bool :=
  match s with
  | .missing | .atom .. | .ident .. => true
  | .node _ k args => Id.run do
    let kind := k.toString false
    if !allowedKinds.contains kind then return false
    if kind == "Lean.Parser.Command.declModifiers" && args.any hasTokens then return false
    if kind == "Lean.Parser.Command.derivingClass" then
      if args.size != 2 || hasTokens args[0]! || !args[1]!.isIdent || !#["Repr", "BEq", "DecidableEq"].contains args[1]!.getId.toString then return false
    if kind == "Lean.Parser.Term.hole" && !pattern then return false
    for i in [:args.size] do
      let inPattern := (pattern && kind == "null") || (kind == "Lean.Parser.Term.matchAlt" && i == 1)
      if !safeSyntax args[i]! inPattern then return false
    return true

partial def atoms (s : Syntax) : List String :=
  match s with
  | .atom _ a => [a]
  | .ident .. => ["IDENT"]
  | .node _ _ xs => xs.toList.flatMap atoms
  | .missing => []

unsafe def main (args : List String) : IO UInt32 := do
  try
    let [path, policyPath, mode] := args | throw (IO.userError "args")
    unless mode == "approved" || mode == "submission" do throw (IO.userError "mode")
    let .ok policy := Json.parse (← IO.FS.readFile policyPath) | throw (IO.userError "policy")
    let imports ← getList policy "allowed_imports"
    let theorems ← getList policy "theorems"
    unless imports.all (fun name => name == "Init" || name.startsWith "Init." || name == "Std" || name.startsWith "Std." || name == "Lean" || name.startsWith "Lean.") do throw (IO.userError "policy")
    let .ok (selected : String) := policy.getObjValAs? String "theorem" | throw (IO.userError "policy")
    if selected.isEmpty || theorems.size != (Std.HashSet.ofArray theorems).size then throw (IO.userError "policy")
    initSearchPath (← findSysroot)
    enableInitializersExecution
    let env ← importModules #[{module := `Lean}] {} (loadExts := true)
    let source ← IO.FS.readFile path
    let ctx := Parser.mkInputContext source path
    let (header, initialPs, initialMsgs) ← Parser.parseHeader ctx
    let mut ps := initialPs
    let mut msgs := initialMsgs
    if msgs.hasErrors then output "rejected"; return 1
    let h := header.raw
    if hasTokens h[0] || hasTokens h[1] then output "rejected"; return 1
    for imp in h[2].getArgs do
      let a := imp.getArgs
      if a.size != 6 || hasTokens a[0]! || hasTokens a[1]! || hasTokens a[3]! || hasTokens a[5]! || !a[4]!.isIdent || !imports.contains a[4]!.getId.toString then
        output "rejected"; return 1
    if mode == "submission" then output "accepted"; return 0
    let mut ns : List String := []
    let mut found : Array String := #[]
    repeat
      let (cmd, next, messages) := Parser.parseCommand ctx {env := env, options := {}} ps msgs
      ps := next; msgs := messages
      if msgs.hasErrors then output "rejected"; return 1
      if cmd.isOfKind ``Parser.Command.eoi then break
      if cmd.isOfKind ``Parser.Command.namespace then
        let name := cmd[1].getId.toString
        ns := ns ++ [name]
      else if cmd.isOfKind ``Parser.Command.end then
        if ns.isEmpty || cmd[1].getArgs.size != 2 || !cmd[1][0].isIdent || hasTokens cmd[1][1] || cmd[1][0].getId.toString != ns.getLast! then output "rejected"; return 1
        ns := ns.dropLast
      else if cmd.isOfKind ``Parser.Command.declaration then
        let decl := cmd[1]
        if decl.isOfKind ``Parser.Command.theorem then
          let full := String.intercalate "." (ns ++ [decl[1][0].getId.toString])
          if (!theorems.isEmpty && !theorems.contains full) || found.contains full || !safeSyntax cmd[0] || !safeSyntax decl[1] || !safeSyntax decl[2] || atoms decl[3] != [":=", "by", "sorry"] then output "rejected"; return 1
          found := found.push full
        else if !(decl.isOfKind ``Parser.Command.definition || decl.isOfKind ``Parser.Command.inductive || decl.isOfKind ``Parser.Command.structure) || !safeSyntax cmd then
          output "rejected"; return 1
      else
        output "rejected"; return 1
    if !ns.isEmpty || !found.contains selected || (!theorems.isEmpty && (found.size != theorems.size || theorems.any (!found.contains ·))) then output "rejected"; return 1
    output "accepted"; return 0
  catch _ => output "incomplete"; return 2
"""

EXPORTER_SOURCE = r"""import LeanExport.Basic
open Lean LeanExport

def main (args : List String) : IO Unit := do
  initSearchPath (← findSysroot)
  let [mod] := args | throw (IO.userError "invalid arguments")
  let some name := Syntax.decodeNameLit ("`" ++ mod) | throw (IO.userError "invalid module")
  let env ← importModules #[{module := name}] {} (loadExts := false)
  let some index := env.getModuleIdx? name | throw (IO.userError "module not found")
  let roots := env.constants.toList.filterMap fun (n, _) => if env.getModuleIdxFor? n == some index then some n else none
  if roots.isEmpty then throw (IO.userError "module has no constants")
  let primitives := #[``Nat.add, ``Nat.sub, ``Nat.mul, ``Nat.pow, ``Nat.gcd, ``Nat.div, ``Nat.mod, ``Nat.beq, ``Nat.ble, ``Nat.land, ``Nat.lor, ``Nat.xor, ``Nat.shiftLeft, ``Nat.shiftRight, ``String.ofList, ``Char.ofNat, ``List, ``eagerReduce, ``Nat, ``String, ``String.mk, ``Char, ``optParam, ``autoParam, ``semiOutParam, ``outParam, ``propext, ``Classical.choice, ``Quot.sound, ``Quot, ``Quot.mk, ``Quot.lift, ``Quot.ind]
  M.run env do
    initState env
    IO.println (Json.mkObj [("meta", Json.mkObj [("exporter", Json.mkObj [("name", "lean4export"), ("version", "3.1.0")]), ("lean", Json.mkObj [("version", versionString), ("githash", githash)]), ("format", Json.mkObj [("version", "3.1.0")])]), ("mentat_roots", toJson (roots.map toString))]).compress
    for c in roots ++ primitives.toList do
      modify fun st => { st with noMDataExprs := {} }
      dumpConstant c
"""

AUDIT_SOURCE = r"""import LeanExport.Parse
import Lake.Check.Axioms
import Lake.Check.Compare
open Lean LeanExport Lake.Check

def nameOf (s : String) : Except String Name :=
  match Syntax.decodeNameLit ("`" ++ s) with
  | some n => .ok n
  | none => .error "invalid name"

def strField (j : Lean.Json) (key : String) : IO String := do
  let .obj data := j | throw (IO.userError "invalid JSON")
  let some (.str value) := data[key]? | throw (IO.userError "missing field")
  return value

def listField (j : Lean.Json) (key : String) : IO (Array String) := do
  let .obj data := j | throw (IO.userError "invalid JSON")
  let some (.arr values) := data[key]? | throw (IO.userError "missing field")
  values.mapM fun value => match value with
    | .str s => pure s
    | _ => throw (IO.userError "invalid field")

def readExport (path : String) : IO (ExportedEnv × Array Name) := do
  let header ← IO.FS.Stream.ofHandle <$> IO.FS.Handle.mk path IO.FS.Mode.read
  let line ← header.getLine
  let .ok json := Lean.Json.parse line | throw (IO.userError "invalid metadata")
  let names ← listField json "mentat_roots"
  if names.isEmpty then throw (IO.userError "missing roots")
  let roots ← names.mapM fun x => match nameOf x with
    | .ok n => pure n
    | .error _ => throw (IO.userError "invalid root")
  if roots.size != (Std.HashSet.ofArray roots).size then throw (IO.userError "duplicate roots")
  let stream ← IO.FS.Stream.ofHandle <$> IO.FS.Handle.mk path IO.FS.Mode.read
  let result ← parseStream stream
  return (result, roots)

def refs (info : ConstantInfo) : Array Name :=
  (runForUsedConsts info fun n => modify (fun (a : Array Name) => a.push n) : StateM (Array Name) Unit).run #[] |>.2

partial def auditFrom (env : ExportedEnv) (roots : Array Name) (legal : Array Name) : Except String Unit := do
  let mut work := roots
  let mut seen : Std.HashSet Name := {}
  while !work.isEmpty do
    let name := work.back!
    work := work.pop
    if !seen.contains name then
      seen := seen.insert name
      let some info := env.constMap[name]? | throw "missing dependency"
      if let .axiomInfo val := info then
        unless legal.contains val.name do throw "forbidden axiom"
      work := work ++ refs info

def auditModule (env : ExportedEnv) (roots : Array Name) (targets : Array Name) (legal : Array Name) (allowedHoles : Array Name) : Except String Unit := do
  for t in targets do
    unless roots.contains t do throw "missing target"
    let some (.thmInfo _) := env.constMap[t]? | throw "target is not theorem"
  for root in roots do
    let some info := env.constMap[root]? | throw "missing root"
    if let .axiomInfo _ := info then throw "local axiom"
    if info.isUnsafe then throw "unsafe root"
    let permitted := if let .thmInfo _ := info then
      if allowedHoles.contains root then legal.push ``sorryAx else legal
    else legal
    auditFrom env #[root] permitted

def isErr {α : Type} (result : Except String α) : Bool :=
  match result with
  | .error _ => true
  | .ok _ => false

def main (args : List String) : IO UInt32 := do
  try
    let (approvedPath, solutionPath, policyPath, approvedOnly) ← match args with
      | [a, b, p] => pure (a, b, p, false)
      | [a, b, p, "approved"] => pure (a, b, p, true)
      | _ => throw (IO.userError "args")
    let .ok policy := Lean.Json.parse (← IO.FS.readFile policyPath) | throw (IO.userError "policy")
    let targetText ← strField policy "theorem"
    let targetTexts ← listField policy "theorems"
    let axiomTexts ← listField policy "permitted_axioms"
    let .ok target := nameOf targetText | throw (IO.userError "target")
    let .ok declaredTargets := targetTexts.mapM nameOf | throw (IO.userError "targets")
    let .ok legal := axiomTexts.mapM nameOf | throw (IO.userError "axioms")
    let (approved, approvedRoots) ← readExport approvedPath
    let (solution, solutionRoots) ← readExport solutionPath
    let targets : Array Name := if declaredTargets.isEmpty then approvedRoots.filter (fun (name : Name) =>
      match (approved.constMap[name]? : Option ConstantInfo) with
      | some (ConstantInfo.thmInfo _) => isErr (auditFrom approved #[name] legal)
      | _ => false) else declaredTargets
    unless targets.contains target && targets.size == (Std.HashSet.ofArray targets).size &&
      legal.size == (Std.HashSet.ofArray legal).size && legal.all (#[``propext, ``Classical.choice, ``Quot.sound].contains ·) do throw (IO.userError "policy")
    let approvedResult := auditModule approved approvedRoots targets legal targets
    if isErr approvedResult then
      IO.println "{\"status\":\"incomplete\",\"detail\":\"approved audit failed\"}"; return 2
    if approvedOnly then
      IO.println "{\"status\":\"accepted\",\"detail\":\"audit passed\"}"; return 0
    let holes := targets.filter fun name => isErr (auditFrom approved #[name] legal)
    let solutionResult := auditModule solution solutionRoots targets legal (holes.filter (· != target))
    if isErr solutionResult then
      IO.println "{\"status\":\"rejected\",\"detail\":\"audit failed\"}"; return 1
    let primitives := #[``Nat.add, ``Nat.sub, ``Nat.mul, ``Nat.pow, ``Nat.gcd, ``Nat.div, ``Nat.mod, ``Nat.beq, ``Nat.ble, ``Nat.land, ``Nat.lor, ``Nat.xor, ``Nat.shiftLeft, ``Nat.shiftRight, ``String.ofList, ``Char.ofNat, ``List, ``eagerReduce, ``Nat, ``String, ``String.mk, ``Char, ``optParam, ``autoParam, ``semiOutParam, ``outParam]
    match compareAt approved solution (targets ++ legal) #[] primitives with
    | .ok _ =>
      match checkAxioms solution #[target] #[] legal with
      | .ok _ => IO.println "{\"status\":\"accepted\",\"detail\":\"audit passed\"}"; return 0
      | .error _ => IO.println "{\"status\":\"incomplete\",\"detail\":\"axiom audit unavailable\"}"; return 2
    | .error msg =>
      if msg.startsWith "Challenge and solution theorem statement do not match:" || msg.startsWith "Const does not match between challenge and target " || msg.startsWith "Challenge and solution constant kind don't match:" then
        IO.println "{\"status\":\"rejected\",\"detail\":\"comparison mismatch\"}"; return 1
      else
        IO.println "{\"status\":\"incomplete\",\"detail\":\"comparison unavailable\"}"; return 2
  catch _ => IO.println "{\"status\":\"incomplete\",\"detail\":\"audit unavailable\"}"; return 2
"""
