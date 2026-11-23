"""Packaged profiles, `profile` in atta/config.json and `atta profiles` (F2.y T3, S6, S2)."""

import contextlib
import copy
import io
import json
import shutil
import unittest

import support  # noqa: F401
from support import TempDirMixin

from atta.spine import cli, config, initcmd, models, profiles, project

FACTORY = {"T0": "haiku", "T1": "haiku", "T2": "sonnet", "T3": "opus"}
COST = models.COST
NO_PROFILE = "Perfil actual: ninguno (modelos de fábrica)."


class ProfilesBase(TempDirMixin, unittest.TestCase):
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

    def ok(self, *argv):
        code, out, err = self.atta(*argv)
        self.assertEqual((code, err), (0, ""), out)
        return out

    def stored(self):
        return json.loads(self.cfg_path.read_text(encoding="utf-8"))

    def write(self, data):
        self.cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in sorted(self.root.rglob("*")) if p.is_file()}

    def agent_model(self, role):
        text = (self.root / ".claude" / "agents" / f"atta-{role}.md").read_text(encoding="utf-8")
        return [ln.split(":", 1)[1].strip() for ln in text.splitlines()
                if ln.startswith("model:")][0]

    def models_json(self):
        return json.loads(self.ok("models", "--json"))


class PackagedProfilesTest(unittest.TestCase):
    def test_the_three_profiles_are_packaged_and_valid(self):
        found = profiles.load()
        self.assertEqual(list(found), ["economico", "equilibrado", "calidad"])
        for entry in found.values():
            self.assertEqual(sorted(entry), ["delivery", "description", "models"])

    def test_equilibrado_is_the_factory_models_with_a_prudent_delivery(self):
        # L9 (usuario): «low (Recomendado)»: factory models; Atta merges only low-risk jobs.
        entry = profiles.load()["equilibrado"]
        self.assertEqual(entry["models"]["claude-code"], models.default_tiers())
        self.assertEqual(entry["models"]["claude-code"], FACTORY)
        self.assertEqual(entry["delivery"]["auto_merge_up_to"], "low")

    def test_no_profile_merges_above_low(self):  # S6/L9: a profile never loosens past low
        for name, entry in profiles.load().items():
            with self.subTest(profile=name):
                self.assertIn(entry["delivery"]["auto_merge_up_to"], ("none", "low"))

    def test_calidad_never_merges_and_is_never_cheaper_than_factory(self):
        entry = profiles.load()["calidad"]
        self.assertEqual(entry["delivery"]["auto_merge_up_to"], "none")
        for tier, model in entry["models"]["claude-code"].items():
            self.assertGreaterEqual(COST[model], COST[FACTORY[tier]], tier)

    def test_economico_never_merges_above_low_and_keeps_a_strong_t3(self):  # F4 S2
        entry = profiles.load()["economico"]
        self.assertIn(entry["delivery"]["auto_merge_up_to"], ("none", "low"))
        self.assertGreaterEqual(COST[entry["models"]["claude-code"]["T3"]], COST["sonnet"])
        for tier, model in entry["models"]["claude-code"].items():
            self.assertLessEqual(COST[model], COST[FACTORY[tier]], tier)

    def test_accented_names_and_case_are_aliases_of_the_ascii_id(self):
        for name, ident in (("económico", "economico"), ("Económico", "economico"),
                            ("ECONOMICO", "economico"), (" calidad ", "calidad"),
                            ("Equilibrado", "equilibrado")):
            self.assertEqual(profiles.resolve_name(name), ident)
        with self.assertRaises(profiles.ProfilesError) as ctx:
            profiles.resolve_name("barato")
        self.assertEqual(str(ctx.exception),
                         "El perfil «barato» no existe: usa economico, equilibrado o calidad.")


class ProfilesFileValidationTest(TempDirMixin, unittest.TestCase):
    def templates(self, data):
        (self.tmp / "claude").mkdir(exist_ok=True)
        text = data if isinstance(data, str) else json.dumps(data)
        (self.tmp / "claude" / "profiles.json").write_text(text, encoding="utf-8")
        return self.tmp

    def good(self):
        return copy.deepcopy({k: v for k, v in profiles.load().items() if k == "calidad"})

    def test_a_valid_file_loads(self):
        self.assertEqual(list(profiles.load(self.templates(self.good()))), ["calidad"])

    def test_invalid_files_are_refused_with_a_spanish_message(self):
        bad = self.good()
        bad["calidad"]["models"]["claude-code"]["T1"] = "gpt-4"
        cases = [("no es json", "La plantilla profiles.json no es JSON válido"),
                 ({}, "profiles.json debe ser un objeto con al menos un perfil."),
                 ({"Calidad": self.good()["calidad"]}, "debe ir en minúsculas y sin acentos"),
                 (bad, "profiles.json: calidad.models.claude-code.T1: «gpt-4» no es un modelo"),
                 ({"calidad": {"description": "x"}}, "profiles.json: calidad debe ser un objeto")]
        mut = self.good()
        mut["calidad"]["delivery"]["auto_merge_up_to"] = "todo"
        cases.append((mut, "profiles.json: calidad.delivery debe ser"))
        mut = self.good()
        del mut["calidad"]["models"]["claude-code"]["T3"]
        cases.append((mut, "calidad.models.claude-code debe definir T0, T1, T2, T3"))
        for data, msg in cases:
            with self.subTest(msg=msg):
                with self.assertRaises(profiles.ProfilesError) as ctx:
                    profiles.load(self.templates(data))
                self.assertIn(msg, str(ctx.exception))

    def test_missing_file(self):
        with self.assertRaises(profiles.ProfilesError) as ctx:
            profiles.load(self.tmp / "nada")
        self.assertIn("No se pudo leer la plantilla profiles.json", str(ctx.exception))


class PrecedenceTest(ProfilesBase):
    """S6: factory < profile < the project's models overrides; `atta models` shows the origin."""

    def tiers_of(self, cfg):
        return models.table(cfg).tiers

    def test_a_project_without_profile_resolves_exactly_as_before(self):  # PRESERVE
        before = self.ok("models")
        data = self.models_json()
        self.assertIsNone(data["profile"])
        self.assertEqual({t: v["model"] for t, v in data["tiers"].items()}, FACTORY)
        self.assertEqual({v["origin"] for v in data["tiers"].values()}, {"fábrica"})
        self.assertNotIn("Perfil:", before)
        self.assertNotIn("(perfil)", before)
        self.assertEqual(self.tiers_of({}), FACTORY)

    def test_profile_beats_factory_and_models_shows_perfil(self):
        self.write({**self.stored(), "profile": "economico"})
        data = self.models_json()
        self.assertEqual(data["profile"], "economico")
        self.assertEqual({t: v["model"] for t, v in data["tiers"].items()},
                         {"T0": "haiku", "T1": "haiku", "T2": "haiku", "T3": "sonnet"})
        self.assertEqual({t: v["origin"] for t, v in data["tiers"].items()},
                         {"T0": "fábrica", "T1": "fábrica", "T2": "perfil", "T3": "perfil"})
        self.assertEqual(data["tiers"]["T2"]["default"], "sonnet")
        self.assertEqual(data["roles"]["guia"]["origin"], "perfil")
        self.assertEqual(data["roles"]["guia"]["model"], "haiku")
        self.assertEqual(data["roles"]["reina"]["origin"], "perfil")
        self.assertEqual(data["roles"]["reina"]["escalates_to"], "sonnet")
        self.assertEqual(data["roles"]["exploradora"]["origin"], "tier")
        text = self.ok("models")
        self.assertIn("Perfil: economico (`atta profiles`); tus overrides de `atta models` "
                      "ganan sobre el perfil.\n", text)
        self.assertIn("Tiers: T0 haiku · T1 haiku · T2 haiku (perfil) · T3 sonnet (perfil)\n",
                      text)
        self.assertRegex(text, r"(?m)^guia\s+T2\s+haiku\s+perfil$")
        self.assertRegex(text, r"(?m)^reina\s+T2\s+haiku\s+perfil · escala a T3 \(sonnet\)$")

    def test_tier_override_beats_the_profile(self):
        cfg = self.stored()
        cfg["profile"] = "calidad"
        cfg["models"] = {"claude-code": {"tiers": {"T2": "haiku"}}}
        self.write(cfg)
        data = self.models_json()
        self.assertEqual(data["tiers"]["T2"], {"default": "sonnet", "model": "haiku",
                                               "origin": "proyecto"})
        self.assertEqual(data["tiers"]["T1"]["model"], "sonnet")
        self.assertEqual(data["tiers"]["T1"]["origin"], "perfil")
        self.assertEqual(data["tiers"]["T3"]["origin"], "fábrica")
        self.assertIn("T2 haiku (proyecto)", self.ok("models"))

    def test_role_override_beats_the_profile(self):
        cfg = self.stored()
        cfg["profile"] = "calidad"
        cfg["models"] = {"claude-code": {"roles": {"guia": "haiku"}}}
        self.write(cfg)
        data = self.models_json()
        self.assertEqual(data["roles"]["guia"]["model"], "haiku")
        self.assertEqual(data["roles"]["guia"]["origin"], "rol")
        self.assertEqual(data["roles"]["guia"]["override"], "haiku")
        self.assertEqual(data["roles"]["cortadora"]["model"], "opus")
        self.assertEqual(data["roles"]["cortadora"]["origin"], "perfil")

    def test_equilibrado_changes_nothing_in_the_effective_models(self):
        plain = self.models_json()
        self.write({**self.stored(), "profile": "equilibrado"})
        data = self.models_json()
        self.assertEqual(data.pop("profile"), "equilibrado")
        plain.pop("profile")
        self.assertEqual(data, plain)
        shown = self.ok("models")
        self.assertNotIn("(perfil)", shown)
        self.assertRegex(shown, r"(?m)^guia\s+T2\s+sonnet\s+tier$")

    def test_explicit_delivery_key_is_what_the_pipeline_reads(self):
        cfg = self.stored()
        cfg["profile"] = "calidad"
        cfg["delivery"] = {"auto_merge_up_to": "medium"}
        self.write(cfg)
        loaded = project.load_config(self.root)
        self.assertEqual(loaded["delivery"]["auto_merge_up_to"], "medium")  # profile never wins
        self.assertTrue(config.auto_merges(loaded, "medium"))

    def test_unknown_profile_in_config_is_refused_by_models(self):
        self.write({**self.stored(), "profile": "barato"})
        code, out, err = self.atta("models")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("config.json: profile «barato» no existe: usa economico, equilibrado o "
                      "calidad.", err)


class ListAndShowTest(ProfilesBase):
    def test_list_text_without_profile(self):
        out = self.ok("profiles")
        self.assertEqual(out, f"""\
Perfiles de Atta en {self.root} (runtime claude-code):
  economico
    T0 haiku · T1 haiku · T2 haiku · T3 sonnet · entrega hasta low
    {profiles.load()['economico']['description']}
  equilibrado
    T0 haiku · T1 haiku · T2 sonnet · T3 opus · entrega hasta low
    {profiles.load()['equilibrado']['description']}
  calidad
    T0 haiku · T1 sonnet · T2 opus · T3 opus · entrega hasta none
    {profiles.load()['calidad']['description']}
{NO_PROFILE}
Mira el efecto con `atta profiles show <perfil>`; elige con `atta profiles use <perfil>` o vuelve a fábrica con `atta profiles clear`.
— Atta
""")

    def test_list_marks_the_current_profile(self):
        self.ok("profiles", "use", "calidad")
        out = self.ok("profiles")
        self.assertIn("\n* calidad\n", out)
        self.assertIn("\n  economico\n", out)
        self.assertIn("Perfil actual: calidad.\n", out)

    def test_list_does_not_write(self):
        before = self.snapshot()
        self.ok("profiles")
        self.ok("profiles", "--json")
        self.ok("profiles", "show", "calidad")
        self.assertEqual(self.snapshot(), before)

    def test_json_state(self):
        out = self.ok("profiles", "--json")
        data = json.loads(out)
        self.assertEqual(out, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
                         + "\n")
        self.assertEqual(sorted(data), ["current", "delivery", "profiles", "root", "runtime"])
        self.assertIsNone(data["current"])
        self.assertEqual(data["delivery"], "none")  # what atta init wrote
        self.assertEqual(sorted(data["profiles"]), ["calidad", "economico", "equilibrado"])
        eco = data["profiles"]["economico"]
        self.assertEqual(sorted(eco), ["current", "delivery", "description", "effect", "models"])
        self.assertEqual(eco["models"], {"T0": "haiku", "T1": "haiku", "T2": "haiku",
                                         "T3": "sonnet"})
        self.assertEqual(eco["effect"]["tiers"], {"T2": {"from": "sonnet", "to": "haiku"},
                                                  "T3": {"from": "opus", "to": "sonnet"}})
        self.assertEqual(eco["effect"]["delivery"], {"from": "none", "to": "low"})
        self.assertEqual(eco["effect"]["agents"]["guia"], {"from": "sonnet", "to": "haiku"})
        self.assertEqual([p["current"] for p in data["profiles"].values()],
                         [False, False, False])

    def test_json_marks_current_and_the_effect_of_it_is_empty(self):
        self.ok("profiles", "use", "calidad")
        data = json.loads(self.ok("profiles", "--json"))
        self.assertEqual(data["current"], "calidad")
        self.assertTrue(data["profiles"]["calidad"]["current"])
        eff = data["profiles"]["calidad"]["effect"]
        self.assertEqual((eff["tiers"], eff["agents"], eff["delivery"]),
                         ({}, {}, {"from": "none", "to": "none"}))

    def test_show_previews_the_effect(self):
        d = profiles.load()["calidad"]["description"]
        out = self.ok("profiles", "show", "Calidad")
        expected = f"""\
Perfil calidad: {d}
  Modelos: T0 haiku · T1 sonnet · T2 opus · T3 opus
  Entrega: Atta mergea sola hasta riesgo none
Efecto de elegir calidad sobre la configuración actual (perfil actual: ninguno):
  T1: haiku → sonnet
  T2: sonnet → opus
AGENTS
Elígelo con `atta profiles use calidad`.
— Atta
"""
        head, tail = expected.split("AGENTS\n")
        self.assertTrue(out.startswith(head), out)
        self.assertTrue(out.endswith(tail), out)
        agents = out[len(head):-len(tail)]
        self.assertRegex(agents, r"^  agentes que cambian de modelo: exploradora haiku → sonnet, "
                                 r"guia sonnet → opus, .*reina sonnet → opus\n$")
        self.assertIn("vigia haiku → sonnet", agents)

    def test_show_reports_overrides_that_win_and_no_change(self):
        self.ok("models", "set", "T2", "haiku")
        self.ok("models", "set", "--role", "soldado", "haiku")
        out = self.ok("profiles", "show", "calidad")
        self.assertIn("  tus overrides de atta models ganan sobre el perfil: T2 se queda en "
                      "haiku\n", out)
        self.assertIn("  roles con modelo propio, que el perfil no toca: soldado\n", out)
        self.ok("profiles", "use", "equilibrado")
        self.write({k: v for k, v in self.stored().items() if k != "models"})
        out = self.ok("profiles", "show", "equilibrado")
        self.assertIn("(perfil actual: equilibrado):\n  Sin cambios: ya tienes estos modelos y "
                      "esta entrega.\n", out)

    def test_unknown_name_in_show_is_refused(self):
        before = self.snapshot()
        code, out, err = self.atta("profiles", "show", "barato")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "El perfil «barato» no existe: usa economico, equilibrado o "
                              "calidad. No cambié nada.\n")
        self.assertEqual(self.snapshot(), before)

    def test_json_with_an_action_is_refused(self):
        before = self.snapshot()
        code, out, err = self.atta("profiles", "--json", "use", "calidad")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "--json solo se usa sin acción (atta profiles --json). No cambié "
                              "nada.\n")
        self.assertEqual(self.snapshot(), before)


class UseAndClearTest(ProfilesBase):
    def test_use_writes_profile_and_delivery_and_syncs_agents(self):
        d = profiles.load()["economico"]
        code, out, err = self.atta("profiles", "use", "económico")
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(lines[0],
                         "Atta: el perfil economico queda fijo en atta/config.json: T0 haiku · "
                         "T1 haiku · T2 haiku · T3 sonnet, y la entrega "
                         "(delivery.auto_merge_up_to) queda escrita: none → low. Tus overrides "
                         "de atta models siguen ganando sobre el perfil.")
        self.assertIn("  actualicé: .claude/agents/atta-guia.md → haiku", lines)
        self.assertIn("  actualicé: .claude/agents/atta-reina.md → haiku", lines)
        self.assertEqual(lines[-2:], ["Revisa la tabla con `atta models`.", "— Atta"])
        cfg = self.stored()
        self.assertEqual(cfg["profile"], "economico")
        self.assertEqual(cfg["delivery"], d["delivery"])
        self.assertEqual(self.agent_model("guia"), "haiku")
        self.assertEqual(self.agent_model("rastreador"), "sonnet")
        self.assertEqual(self.ok("init", "--check").count("al día"), 1)  # no drift

    def test_use_keeps_other_keys_and_their_order(self):
        cfg = self.stored()
        keys = list(cfg)
        cfg["max_rounds"] = 5
        self.write(cfg)
        self.ok("profiles", "use", "calidad")
        after = self.stored()
        self.assertEqual(list(after)[:len(keys)], keys)
        self.assertEqual(after["max_rounds"], 5)

    def test_explicit_delivery_is_overwritten_only_by_use_and_the_effect_says_so(self):
        # decision: the profile writes delivery at `use` time (it never overrides silently)
        cfg = self.stored()
        cfg["delivery"] = {"auto_merge_up_to": "medium"}
        self.write(cfg)
        data = json.loads(self.ok("profiles", "--json"))
        self.assertEqual(data["profiles"]["calidad"]["effect"]["delivery"],
                         {"from": "medium", "to": "none"})
        self.assertIn("  entrega (delivery.auto_merge_up_to): medium → none\n",
                      self.ok("profiles", "show", "calidad"))
        self.assertEqual(self.stored()["delivery"], {"auto_merge_up_to": "medium"})  # still
        out = self.ok("profiles", "use", "calidad")
        self.assertIn("queda escrita: medium → none.", out)
        self.assertEqual(self.stored()["delivery"], {"auto_merge_up_to": "none"})
        # after use, a later explicit choice wins: the pipeline reads the key only
        self.ok("settings", "set", "delivery.auto_merge_up_to", "low")
        self.assertEqual(self.stored()["profile"], "calidad")
        self.assertEqual(project.load_config(self.root)["delivery"]["auto_merge_up_to"], "low")

    def test_use_without_a_delivery_key_writes_it(self):
        cfg = self.stored()
        del cfg["delivery"]
        self.write(cfg)
        out = self.ok("profiles", "use", "equilibrado")
        self.assertIn("queda escrita: high → low.", out)
        self.assertEqual(self.stored()["delivery"], {"auto_merge_up_to": "low"})

    def test_use_keeps_models_overrides_winning(self):
        self.ok("models", "set", "T2", "haiku")
        self.ok("profiles", "use", "calidad")
        self.assertEqual(self.stored()["models"]["claude-code"]["tiers"], {"T2": "haiku"})
        self.assertEqual(self.agent_model("guia"), "haiku")
        self.assertEqual(self.agent_model("exploradora"), "sonnet")  # T1 from the profile

    def test_equilibrado_on_a_project_without_profile_changes_no_model(self):
        agents = {r: self.agent_model(r) for r in models.ROLES}
        tiers = self.models_json()["tiers"]
        out = self.ok("profiles", "use", "equilibrado")
        self.assertIn("  sin cambios: 12 agentes", out)
        self.assertNotIn("actualicé", out)
        self.assertEqual({r: self.agent_model(r) for r in models.ROLES}, agents)
        after = self.models_json()["tiers"]
        self.assertEqual(after, tiers)

    def test_use_same_profile_twice_is_a_no_op(self):
        self.ok("profiles", "use", "calidad")
        before = self.snapshot()
        out = self.ok("profiles", "use", "Calidad")
        self.assertEqual(out, "Atta: el proyecto ya usa el perfil calidad; no cambié nada.\n"
                              "— Atta\n")
        self.assertEqual(self.snapshot(), before)

    def test_use_unknown_name_exits_2_and_writes_nothing(self):
        before = self.snapshot()
        for name in ("barato", "", "cali dad"):
            with self.subTest(name=name):
                code, out, err = self.atta("profiles", "use", name)
                self.assertEqual((code, out), (2, ""))
                self.assertEqual(err, f"El perfil «{name}» no existe: usa economico, "
                                      "equilibrado o calidad. No cambié nada.\n")
                self.assertEqual(self.snapshot(), before)

    def test_use_and_clear_usage_errors_exit_2_and_write_nothing(self):
        before = self.snapshot()
        for argv in (("use",), ("use", "calidad", "otro"), ("clear", "calidad"),
                     ("show",), ("borrar",)):
            with self.subTest(argv=argv):
                code, out, err = self.atta("profiles", *argv)
                self.assertEqual((code, out), (2, ""))
                self.assertNotEqual(err, "")
                self.assertEqual(self.snapshot(), before)

    def test_use_refuses_an_unreadable_config_without_writing(self):
        self.cfg_path.write_text("{no", encoding="utf-8")
        before = self.snapshot()
        code, out, err = self.atta("profiles", "use", "calidad")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(self.snapshot(), before)

    def test_clear_removes_the_profile_keeps_delivery_and_resyncs(self):
        self.ok("profiles", "use", "economico")
        out = self.ok("profiles", "clear")
        self.assertTrue(out.startswith(
            "Atta quitó el perfil economico de atta/config.json: vuelven los modelos de fábrica "
            "(con tus overrides de atta models). La entrega sigue como está (low); cámbiala con "
            "`atta settings set delivery.auto_merge_up_to <nivel>`.\n"), out)
        self.assertIn("  actualicé: .claude/agents/atta-guia.md → sonnet", out)
        cfg = self.stored()
        self.assertNotIn("profile", cfg)
        self.assertEqual(cfg["delivery"], {"auto_merge_up_to": "low"})
        self.assertEqual(self.agent_model("guia"), "sonnet")
        self.assertEqual({t: v["model"] for t, v in self.models_json()["tiers"].items()},
                         FACTORY)

    def test_clear_without_profile_is_a_no_op(self):
        before = self.snapshot()
        self.assertEqual(self.ok("profiles", "clear"),
                         "Atta: el proyecto no usa un perfil; no cambié nada.\n— Atta\n")
        self.assertEqual(self.snapshot(), before)

    def test_models_reset_keeps_the_profile(self):
        self.ok("profiles", "use", "calidad")
        self.ok("models", "set", "T2", "haiku")
        self.ok("models", "reset")
        self.assertEqual(self.stored()["profile"], "calidad")
        self.assertEqual(self.models_json()["tiers"]["T2"]["model"], "opus")

    def test_help_and_registry(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            cli.build_parser().parse_args(["profiles", "--help"])
        self.assertEqual(ctx.exception.code, 0)
        for word in ("economico", "equilibrado", "calidad", "use", "clear", "--json"):
            self.assertIn(word, out.getvalue())


if __name__ == "__main__":
    unittest.main()
