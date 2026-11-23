"""`atta models` and models.resolve (S5, S12, L18): per-project model calibration."""

import contextlib
import io
import json
import unittest
from pathlib import Path

import support  # noqa: F401
from support import TempDirMixin

from atta.spine import cli, config, initcmd, models

ROLES = ("exploradora", "guia", "vigia", "cortadora", "jardinera-code", "jardinera-resilience",
         "jardinera-ux", "jardinera-ui", "soldado", "rastreador", "refutador", "reina")
T2_ROLES = ("guia", "cortadora", "jardinera-code", "jardinera-resilience", "jardinera-ux",
            "soldado", "reina")
DEFAULT_TABLE = """\
ROL                   TIER  MODELO  ORIGEN
exploradora           T1    haiku   tier
guia                  T2    sonnet  tier
vigia                 T2    haiku   regla ≠ guía
cortadora             T2    sonnet  tier · escala a T3 (opus)
jardinera-code        T2    sonnet  tier
jardinera-resilience  T2    sonnet  tier
jardinera-ux          T2    sonnet  tier
jardinera-ui          T1    haiku   tier
soldado               T2    sonnet  tier · escala a T3 (opus)
rastreador            T3    opus    tier
refutador             T1    haiku   tier
reina                 T2    sonnet  tier · escala a T3 (opus)
Tiers: T0 haiku · T1 haiku · T2 sonnet · T3 opus
Las Jardineras, el Soldado y el Rastreador corren en el tier que les asigna el router; un modelo por rol lo reemplaza.
El orquestador /atta (comando y skill) corre en T0 (haiku).
Fallback por cuota, rate limit o red: ninguno (`atta models fallback <tier> <modelo>...`).
Cambia con `atta models set <T0|T1|T2|T3> <modelo>` o `atta models set --role <rol> <modelo>`.
— Atta
"""
DRIVER = (".claude/commands/atta.md", ".claude/skills/atta/SKILL.md")
BAD_MODEL = ("«gpt-4» no es un modelo válido para claude-code: usa haiku, sonnet u opus, o un id "
             "completo que cumpla claude-[a-z0-9.-]+ (p. ej. claude-sonnet-4-5). No cambié nada.\n")


class ModelsBase(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root = (self.tmp / "proj").resolve()
        self.root.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(initcmd.run(self.root, no_github=True), 0)
        self.cfg_path = self.root / "atta" / "config.json"

    def atta(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--project", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def agent_model(self, role):
        text = (self.root / ".claude" / "agents" / f"atta-{role}.md").read_text(encoding="utf-8")
        return [ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("model:")]

    def files(self):
        out = {"config": self.cfg_path.read_bytes()}
        for p in sorted((self.root / ".claude" / "agents").glob("*.md")):
            out[p.name] = p.read_bytes()
        return out

    def stored(self):
        return json.loads(self.cfg_path.read_text(encoding="utf-8"))


class TableTest(ModelsBase):
    def test_defaults_table(self):
        code, out, err = self.atta("models")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, f"Modelos de Atta en {self.root} (runtime claude-code):\n"
                              + DEFAULT_TABLE)

    def test_runtime_option_is_accepted(self):
        self.assertEqual(self.atta("models", "--runtime", "claude-code")[1],
                         self.atta("models")[1])

    def test_unknown_runtime_is_a_usage_error(self):
        code, out, _ = self.atta("models", "--runtime", "otro")
        self.assertEqual((code, out), (2, ""))

    def test_role_default_tiers_come_from_the_templates(self):
        self.assertEqual(models.role_tiers(), {
            "exploradora": "T1", "guia": "T2", "vigia": "T2", "cortadora": "T2",
            "jardinera-code": "T2", "jardinera-resilience": "T2", "jardinera-ux": "T2",
            "jardinera-ui": "T1", "soldado": "T2", "rastreador": "T3", "refutador": "T1",
            "reina": "T2"})

    def test_there_are_twelve_roles_and_the_rastreador_is_t3(self):
        self.assertEqual(models.ROLES, ROLES)
        self.assertEqual(len(models.ROLES), 12)
        tab = models.table(config.with_defaults({}))
        self.assertEqual(tab.tier_of("rastreador"), "T3")
        self.assertEqual(tab.agent_model("rastreador"), "opus")
        self.assertEqual(self.agent_model("rastreador"), ["opus"])

    def test_init_agents_match_the_table(self):
        tab = models.table(config.with_defaults({}))
        for role in ROLES:
            self.assertEqual(self.agent_model(role), [tab.agent_model(role)], role)


class SetTest(ModelsBase):
    def test_set_tier_changes_every_role_on_that_tier_and_the_agents(self):
        before = self.stored()
        code, out, err = self.atta("models", "set", "T2", "haiku")
        self.assertEqual((code, err), (0, ""))
        data = self.stored()
        self.assertEqual(data["models"], {"claude-code": {"tiers": {"T2": "haiku"}}})
        # every other key kept, in order
        self.assertEqual([k for k in data if k != "models"], list(before))
        self.assertEqual({k: v for k, v in data.items() if k != "models"}, before)
        for role in T2_ROLES:
            self.assertEqual(self.agent_model(role), ["haiku"], role)
        self.assertEqual(self.agent_model("exploradora"), ["haiku"])
        # the Vigía still avoids the Guía's model: T2 and T1 are haiku, so it goes up to T3
        self.assertEqual(self.agent_model("vigia"), ["opus"])
        self.assertTrue(out.startswith("Atta: T2 ahora es haiku en atta/config.json "
                                       "(claude-code).\n"), out)
        self.assertIn("  actualicé: .claude/agents/atta-guia.md → haiku\n", out)
        self.assertTrue(out.endswith("— Atta\n"), out)
        _, table, _ = self.atta("models")
        self.assertIn("guia                  T2    haiku   tier\n", table)
        self.assertIn("vigia                 T2    opus    regla ≠ guía\n", table)
        self.assertIn("Tiers: T0 haiku · T1 haiku · T2 haiku (proyecto) · T3 opus\n", table)

    def test_set_role_overrides_only_that_role(self):
        code, out, err = self.atta("models", "set", "--role", "soldado", "opus")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.stored()["models"],
                         {"claude-code": {"roles": {"soldado": "opus"}}})
        self.assertEqual(self.agent_model("soldado"), ["opus"])
        for role in T2_ROLES:
            if role != "soldado":
                self.assertEqual(self.agent_model(role), ["sonnet"], role)
        self.assertIn("Atta: soldado ahora usa opus en atta/config.json (claude-code).", out)
        self.assertIn("  actualicé: .claude/agents/atta-soldado.md → opus\n", out)
        self.assertIn("  sin cambios: 11 agentes\n", out)
        self.assertIn("soldado               T2    opus    rol · escala a T3 (opus)\n",
                      self.atta("models")[1])

    def test_role_with_accent_and_full_model_id(self):
        code, _, err = self.atta("models", "set", "--role", "Guía", "claude-sonnet-4-5")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.agent_model("guia"), ["claude-sonnet-4-5"])

    def test_set_role_rastreador_changes_its_agent(self):
        code, out, err = self.atta("models", "set", "--role", "Rastreador", "sonnet")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.stored()["models"],
                         {"claude-code": {"roles": {"rastreador": "sonnet"}}})
        self.assertEqual(self.agent_model("rastreador"), ["sonnet"])
        self.assertIn("  actualicé: .claude/agents/atta-rastreador.md → sonnet\n", out)
        self.assertIn("rastreador            T3    sonnet  rol\n", self.atta("models")[1])

    def test_set_role_refutador_changes_its_agent(self):
        code, out, err = self.atta("models", "set", "--role", "Refutador", "sonnet")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.stored()["models"],
                         {"claude-code": {"roles": {"refutador": "sonnet"}}})
        self.assertEqual(self.agent_model("refutador"), ["sonnet"])
        self.assertIn("  actualicé: .claude/agents/atta-refutador.md → sonnet\n", out)
        self.assertIn("refutador             T1    sonnet  rol\n", self.atta("models")[1])

    def test_tier_override_does_not_beat_a_role_override(self):
        self.atta("models", "set", "--role", "reina", "opus")
        self.atta("models", "set", "T2", "haiku")
        self.assertEqual(self.agent_model("reina"), ["opus"])
        self.assertEqual(self.agent_model("guia"), ["haiku"])

    def test_only_the_model_line_changes(self):
        path = self.root / ".claude" / "agents" / "atta-guia.md"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n") + b"mi nota\r\n")
        before = path.read_bytes()
        self.atta("models", "set", "T2", "haiku")
        self.assertEqual(path.read_bytes(), before.replace(b"model: sonnet\r\n",
                                                           b"model: haiku\r\n"))

    def driver_model(self, rel):
        text = (self.root / rel).read_text(encoding="utf-8")
        front = text.split("---\n", 2)[1]
        return [ln.split(":", 1)[1].strip() for ln in front.splitlines()
                if ln.startswith("model:")]

    def test_set_t0_moves_the_atta_driver_and_no_agent(self):
        # F4 S5 (G8): /atta runs on T0; `models set T0` keeps command and skill in sync.
        agents = {k: v for k, v in self.files().items() if k != "config"}
        code, out, err = self.atta("models", "set", "T0", "sonnet")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "Atta: T0 ahora es sonnet en atta/config.json (claude-code).\n"
                              "  sin cambios: 12 agentes\n"
                              "  actualicé: .claude/skills/atta/SKILL.md → sonnet\n"
                              "  actualicé: .claude/commands/atta.md → sonnet\n"
                              "Revisa la tabla con `atta models`.\n— Atta\n")
        for rel in DRIVER:
            self.assertEqual(self.driver_model(rel), ["sonnet"], rel)
        self.assertEqual({k: v for k, v in self.files().items() if k != "config"}, agents)
        self.assertIn("El orquestador /atta (comando y skill) corre en T0 (sonnet).\n",
                      self.atta("models")[1])
        # what `models set` writes is exactly what `atta init` would write: no drift
        code, out, _ = self.atta("init", "--check")
        self.assertEqual(code, 0, out)
        code, out, err = self.atta("models", "reset")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("  actualicé: .claude/commands/atta.md → haiku\n", out)
        for rel in DRIVER:
            self.assertEqual(self.driver_model(rel), ["haiku"], rel)

    def test_driver_without_model_line_is_reported_and_kept(self):
        # a command from before F4 has no `model:` line: left as is, init --force fixes it
        command = self.root / ".claude" / "commands" / "atta.md"
        old = b"---\ndescription: viejo\n---\n\nCuerpo.\n"
        command.write_bytes(old)
        code, out, err = self.atta("models", "set", "T0", "sonnet")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("  sin línea model: en .claude/commands/atta.md; la dejé igual (ponlo al "
                      "día con `atta init --force`).\n", out)
        self.assertEqual(command.read_bytes(), old)
        self.assertEqual(self.driver_model(DRIVER[1]), ["sonnet"])

    def test_missing_driver_file_is_reported(self):
        (self.root / ".claude" / "skills" / "atta" / "SKILL.md").unlink()
        code, out, err = self.atta("models", "set", "T0", "sonnet")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("  falta: .claude/skills/atta/SKILL.md; corre `atta init` para crearlo.\n",
                      out)
        self.assertFalse((self.root / ".claude" / "skills" / "atta" / "SKILL.md").exists())

    def test_missing_agent_file_is_reported(self):
        (self.root / ".claude" / "agents" / "atta-reina.md").unlink()
        code, out, err = self.atta("models", "set", "T2", "haiku")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("  falta: .claude/agents/atta-reina.md; corre `atta init` para crearlo.\n",
                      out)
        self.assertFalse((self.root / ".claude" / "agents" / "atta-reina.md").exists())


class RefusalTest(ModelsBase):
    def assertRefused(self, argv, stderr):
        before = self.files()
        code, out, err = self.atta(*argv)
        self.assertEqual((code, out, err), (2, "", stderr))
        self.assertEqual(self.files(), before)

    def test_invalid_model(self):
        self.assertRefused(("models", "set", "T2", "gpt-4"), BAD_MODEL)
        self.assertRefused(("models", "set", "--role", "soldado", "gpt-4"), BAD_MODEL)
        self.assertRefused(("models", "set", "T2", "Claude-Opus"), BAD_MODEL.replace(
            "gpt-4", "Claude-Opus"))

    def test_invalid_tier(self):
        self.assertRefused(("models", "set", "T9", "haiku"),
                           "El tier «T9» no existe: usa T0, T1, T2 o T3. No cambié nada.\n")

    def test_invalid_role(self):
        msg = ("El rol «obrera» no existe: usa exploradora, guia, vigia, cortadora, "
               "jardinera-code, jardinera-resilience, jardinera-ux, jardinera-ui, soldado, "
               "rastreador, refutador o reina. No cambié nada.\n")
        self.assertRefused(("models", "set", "--role", "obrera", "haiku"), msg)
        self.assertRefused(("models", "unset", "--role", "obrera"), msg)

    def test_wrong_argument_count(self):
        usage = ("Uso: atta models set <T0|T1|T2|T3> <modelo>, o atta models set --role <rol> "
                 "<modelo>. No cambié nada.\n")
        self.assertRefused(("models", "set", "haiku"), usage)
        self.assertRefused(("models", "set", "--role", "reina", "T2", "haiku"), usage)


class UnsetResetTest(ModelsBase):
    def test_unset_role_goes_back_to_its_tier(self):
        self.atta("models", "set", "--role", "soldado", "opus")
        code, out, err = self.atta("models", "unset", "--role", "soldado")
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("models", self.stored())
        self.assertEqual(self.agent_model("soldado"), ["sonnet"])
        self.assertIn("Atta: soldado vuelve al modelo de su tier (sonnet) en atta/config.json.",
                      out)

    def test_unset_without_override_changes_nothing(self):
        before = self.files()
        code, out, err = self.atta("models", "unset", "--role", "soldado")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "Atta: soldado no tiene un modelo propio en claude-code; "
                              "no cambié nada.\n— Atta\n")
        self.assertEqual(self.files(), before)

    def test_reset_restores_the_defaults(self):
        pristine = self.files()
        self.atta("models", "set", "T2", "haiku")
        self.atta("models", "set", "--role", "soldado", "opus")
        code, out, err = self.atta("models", "reset")
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("models", self.stored())
        self.assertEqual({k: v for k, v in self.files().items() if k != "config"},
                         {k: v for k, v in pristine.items() if k != "config"})
        self.assertEqual(self.atta("models")[1].split("\n", 1)[1], DEFAULT_TABLE)
        self.assertIn("Atta quitó «models» de atta/config.json: vuelven los modelos de fábrica "
                      "(T0 haiku · T1 haiku · T2 sonnet · T3 opus).", out)

    def test_reset_without_models_changes_nothing(self):
        before = self.files()
        code, out, err = self.atta("models", "reset")
        self.assertEqual((code, out, err), (0, "Atta: el proyecto ya usa los modelos de "
                                               "fábrica; no cambié nada.\n— Atta\n", ""))
        self.assertEqual(self.files(), before)


class ResolveTest(unittest.TestCase):
    def cfg(self, **entry):
        return config.with_defaults({"models": {"claude-code": entry}} if entry else {})

    def test_defaults(self):
        cfg = self.cfg()
        self.assertEqual(models.resolve("exploradora", cfg), "haiku")
        self.assertEqual(models.resolve("guia", cfg), "sonnet")
        self.assertEqual(models.resolve("cortadora", cfg, escalate=True), "opus")

    def test_floor_never_resolves_weaker_than_what_is_judged(self):
        cfg = self.cfg()
        # judging a haiku (T1) reviewer: a different model, never weaker
        self.assertEqual(models.resolve("refutador", cfg, avoid={"haiku"}, floor="T1"), "sonnet")
        # judging a sonnet (T2) reviewer: only T3 is both different and not weaker
        self.assertEqual(models.resolve("refutador", cfg, avoid={"sonnet"}, floor="T2"), "opus")
        # no floor: the role's own tier
        self.assertEqual(models.resolve("refutador", cfg), "haiku")

    def test_role_override_and_escalation_still_goes_to_t3(self):
        cfg = self.cfg(roles={"cortadora": "haiku"}, tiers={"T3": "claude-opus-4-1"})
        self.assertEqual(models.resolve("cortadora", cfg), "haiku")
        self.assertEqual(models.resolve("cortadora", cfg, escalate=True), "claude-opus-4-1")

    def test_router_tier_is_used_unless_the_role_is_overridden(self):
        self.assertEqual(models.resolve("jardinera-code", self.cfg(), tier="T1"), "haiku")
        cfg = self.cfg(roles={"jardinera-code": "opus"})
        self.assertEqual(models.resolve("jardinera-code", cfg, tier="T1"), "opus")

    def test_vigia_avoids_the_guia(self):
        cfg = self.cfg()
        guia = models.resolve("guia", cfg)
        self.assertEqual(models.resolve("vigia", cfg, avoid=guia), "haiku")
        cfg = self.cfg(roles={"vigia": "sonnet"})
        self.assertEqual(models.resolve("vigia", cfg, avoid="sonnet"), "haiku")
        cfg = self.cfg(tiers={"T0": "opus", "T1": "opus", "T2": "opus", "T3": "opus"})
        self.assertEqual(models.resolve("vigia", cfg, avoid="opus"), "opus")

    def test_refutador_avoids_every_model_it_judges(self):
        cfg = self.cfg()
        self.assertEqual(models.resolve("refutador", cfg), "haiku")
        self.assertEqual(models.resolve("refutador", cfg, avoid={"opus"}), "haiku")
        self.assertEqual(models.resolve("refutador", cfg, avoid={"haiku"}), "sonnet")
        self.assertEqual(models.resolve("refutador", cfg, avoid={"haiku", "sonnet"}), "opus")
        self.assertEqual(models.resolve("refutador", cfg, avoid={"haiku", "sonnet", "opus"}),
                         "haiku")
        cfg = self.cfg(roles={"refutador": "sonnet"})
        self.assertEqual(models.resolve("refutador", cfg), "sonnet")
        self.assertEqual(models.resolve("refutador", cfg, avoid=["sonnet"]), "haiku")



class FallbackTest(ModelsBase):
    """F4 T4 (S4, L4): per-tier Claude fallback chains for quota, rate limit or network
    errors; a fallback never goes to a more expensive model."""

    def test_set_chain_is_stored_and_shown_without_touching_agents(self):
        before = self.files()
        code, out, err = self.atta("models", "fallback", "T3", "sonnet", "haiku")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Atta: si T3 (opus) falla por cuota, rate limit o red, usa sonnet → "
                      "haiku en atta/config.json (claude-code).", out)
        self.assertEqual(self.stored()["models"],
                         {"claude-code": {"fallbacks": {"T3": ["sonnet", "haiku"]}}})
        after = self.files()
        self.assertEqual({k: v for k, v in after.items() if k != "config"},
                         {k: v for k, v in before.items() if k != "config"})
        _code, table, _err = self.atta("models")
        self.assertIn("Fallback por cuota, rate limit o red: T3 opus → sonnet → haiku.\n", table)

    def test_tier_name_is_case_insensitive(self):
        code, _out, _err = self.atta("models", "fallback", "t2", "haiku")
        self.assertEqual(code, 0)
        self.assertEqual(self.stored()["models"]["claude-code"]["fallbacks"], {"T2": ["haiku"]})

    def refused(self, argv, message):
        before = self.files()
        code, out, err = self.atta("models", "fallback", *argv)
        self.assertEqual((code, out, err), (2, "", message))
        self.assertEqual(self.files(), before)

    def test_more_expensive_model_is_refused(self):
        self.refused(("T2", "opus"), "«opus» es más caro que el modelo de T2 (sonnet): un "
                     "fallback nunca sube de costo. No cambié nada.\n")

    def test_more_expensive_full_id_is_refused(self):
        self.refused(("T1", "claude-sonnet-4-5"), "«claude-sonnet-4-5» es más caro que el "
                     "modelo de T1 (haiku): un fallback nunca sube de costo. No cambié nada.\n")

    def test_same_model_as_the_tier_is_refused(self):
        self.refused(("T2", "sonnet"), "«sonnet» ya es el modelo de T2: el fallback necesita "
                     "otro modelo. No cambié nada.\n")

    def test_repeated_model_is_refused(self):
        self.refused(("T3", "haiku", "haiku"),
                     "«haiku» se repite en la cadena de T3. No cambié nada.\n")

    def test_model_of_unknown_cost_is_refused(self):
        self.refused(("T3", "claude-x-1"), "No sé el costo de «claude-x-1»: un fallback "
                     "necesita haiku, sonnet u opus en el nombre. No cambié nada.\n")

    def test_invalid_model_is_refused(self):
        self.refused(("T3", "gpt-4"), BAD_MODEL)

    def test_invalid_tier_is_refused(self):
        self.refused(("T9", "haiku"), "El tier «T9» no existe: usa T0, T1, T2 o T3. "
                     "No cambié nada.\n")

    def test_missing_model_is_a_usage_refusal(self):
        self.refused(("T3",), "Uso: atta models fallback <T0|T1|T2|T3> <modelo> "
                     "[<modelo>...], o atta models fallback <T0|T1|T2|T3> --clear. "
                     "No cambié nada.\n")

    def test_clear_with_models_is_a_usage_refusal(self):
        self.refused(("T3", "haiku", "--clear"), "Uso: atta models fallback <T0|T1|T2|T3> "
                     "<modelo> [<modelo>...], o atta models fallback <T0|T1|T2|T3> --clear. "
                     "No cambié nada.\n")

    def test_clear_removes_the_chain_and_prunes(self):
        self.atta("models", "fallback", "T3", "sonnet")
        code, out, err = self.atta("models", "fallback", "T3", "--clear")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Atta: T3 ya no tiene fallback en atta/config.json (claude-code).", out)
        self.assertNotIn("models", self.stored())

    def test_clear_without_chain_changes_nothing(self):
        before = self.files()
        code, out, err = self.atta("models", "fallback", "T3", "--clear")
        self.assertEqual((code, out, err),
                         (0, "Atta: T3 no tiene fallback en claude-code; no cambié nada.\n"
                             "— Atta\n", ""))
        self.assertEqual(self.files(), before)

    def test_set_keeps_tiers_and_roles(self):
        self.atta("models", "set", "--role", "soldado", "opus")
        self.atta("models", "fallback", "T3", "sonnet")
        self.assertEqual(self.stored()["models"]["claude-code"],
                         {"roles": {"soldado": "opus"}, "fallbacks": {"T3": ["sonnet"]}})

    def test_reset_drops_the_chains(self):
        self.atta("models", "fallback", "T3", "sonnet")
        code, _out, _err = self.atta("models", "reset")
        self.assertEqual(code, 0)
        self.assertNotIn("models", self.stored())

    def test_config_error_checks_fallbacks(self):
        cases = {
            '{"T3": "sonnet"}': "models.claude-code.fallbacks.T3 debe ser una lista no vacía "
                                "de modelos.",
            '{"T3": []}': "models.claude-code.fallbacks.T3 debe ser una lista no vacía de "
                          "modelos.",
            '{"T7": ["haiku"]}': "models.claude-code.fallbacks.T7: El tier «T7» no existe: usa "
                                 "T0, T1, T2 o T3.",
            '{"T3": ["claude-x-1"]}': "models.claude-code.fallbacks.T3: No sé el costo de "
                                      "«claude-x-1»: un fallback necesita haiku, sonnet u "
                                      "opus en el nombre.",
            '{"T3": ["haiku", "haiku"]}': "models.claude-code.fallbacks.T3: «haiku» se repite "
                                          "en la cadena de T3.",
            '[]': "models.claude-code.fallbacks debe ser un objeto.",
        }
        for raw, message in cases.items():
            with self.subTest(raw=raw):
                value = {"claude-code": {"fallbacks": json.loads(raw)}}
                self.assertEqual(models.config_error(value), message)
        self.assertIsNone(models.config_error(
            {"claude-code": {"fallbacks": {"T3": ["sonnet", "claude-3-5-haiku-latest"]}}}))


class FallbackResolveTest(unittest.TestCase):
    def table(self, tiers=None, fallbacks=None):
        base = {"T0": "haiku", "T1": "haiku", "T2": "sonnet", "T3": "opus"}
        return models.Table(dict(base, **(tiers or {})), {}, {"reina": "T2"}, base,
                            fallbacks=fallbacks)

    def test_next_model_walks_the_chain_skipping_failed(self):
        tab = self.table(fallbacks={"T3": ["sonnet", "haiku"]})
        self.assertEqual(tab.fallback("T3", {"opus"}), "sonnet")
        self.assertEqual(tab.fallback("T3", {"opus", "sonnet"}), "haiku")
        self.assertIsNone(tab.fallback("T3", {"opus", "sonnet", "haiku"}))

    def test_no_chain_means_no_fallback(self):
        self.assertIsNone(self.table().fallback("T2", {"sonnet"}))

    def test_chain_never_goes_above_the_current_tier_model(self):
        # The tier was lowered after the chain was written: sonnet now costs more than haiku.
        tab = self.table(tiers={"T3": "haiku"}, fallbacks={"T3": ["sonnet", "claude-3-haiku"]})
        self.assertEqual(tab.chain("T3"), ["claude-3-haiku"])
        self.assertEqual(tab.fallback("T3", {"haiku"}), "claude-3-haiku")

    def test_failed_role_override_falls_back_below_its_own_cost(self):
        tab = self.table(fallbacks={"T2": ["opus", "haiku"]})
        self.assertEqual(tab.fallback("T2", {"sonnet"}), "haiku")
        self.assertEqual(tab.fallback("T2", {"opus"}, primary="opus"), "haiku")

    def test_avoided_model_is_skipped_when_another_chain_model_exists(self):
        tab = self.table(fallbacks={"T3": ["sonnet", "haiku"]})
        self.assertEqual(tab.fallback("T3", {"opus"}, avoid=["sonnet"]), "haiku")
        self.assertEqual(tab.fallback("T3", {"opus"}, avoid="sonnet"), "haiku")
        # Only the avoided model is left: it is used (as Table.resolve does), never None.
        self.assertEqual(tab.fallback("T3", {"opus", "haiku"}, avoid=["sonnet"]), "sonnet")
        self.assertEqual(tab.fallback("T3", {"opus"}, avoid=None), "sonnet")

    def test_table_reads_fallbacks_from_config(self):
        tab = models.table({"models": {"claude-code": {"fallbacks": {"T3": ["sonnet"]}}}})
        self.assertEqual(tab.chain("T3"), ["sonnet"])
        self.assertEqual(models.table({}).chain("T3"), [])


if __name__ == "__main__":
    unittest.main()
