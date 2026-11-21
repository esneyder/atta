import unittest

import support  # noqa: F401
from atta.spine import ids


class SlugTests(unittest.TestCase):
    def test_job_id_from_issue_and_title(self):
        self.assertEqual(ids.job_id(12, "Añadir botón de exportación CSV al panel"),
                         "12-anadir-boton-de-exportacion")

    def test_slug_strips_accents_and_symbols_lowercase(self):
        self.assertEqual(ids.slugify("¡Corrección: Ñandú y Pingüino!"), "correccion-nandu-y-pinguino")

    def test_slug_max_four_words(self):
        self.assertEqual(ids.slugify("uno dos tres cuatro cinco seis"), "uno-dos-tres-cuatro")

    def test_title_without_ascii_words_yields_number_only(self):
        self.assertEqual(ids.job_id(7, "🐜 ✨"), "7")

    def test_malicious_title_becomes_safe_id(self):
        jid = ids.job_id(3, "../../etc/passwd; rm -rf /")
        self.assertEqual(jid, "3-etc-passwd-rm-rf")
        self.assertEqual(ids.sanitize_id(jid), jid)

    def test_rejects_non_positive_issue(self):
        with self.assertRaises(ids.InvalidId) as ctx:
            ids.job_id(0, "x")
        self.assertEqual(str(ctx.exception), "Número de issue inválido: 0.")


class SanitizerTests(unittest.TestCase):
    def assertRejected(self, value, message):
        with self.assertRaises(ids.InvalidId) as ctx:
            ids.sanitize_id(value)
        self.assertEqual(str(ctx.exception), message)

    def test_accepts_valid(self):
        self.assertEqual(ids.sanitize_id("12-add-csv"), "12-add-csv")

    def test_rejects_parent_traversal(self):
        self.assertRejected("../x", "Id de job inválido '../x': contiene separadores de ruta.")

    def test_rejects_dotdot_alone(self):
        self.assertRejected("..", "Id de job inválido '..': contiene '..'.")

    def test_rejects_slash(self):
        self.assertRejected("a/b", "Id de job inválido 'a/b': contiene separadores de ruta.")

    def test_rejects_backslash(self):
        self.assertRejected("a\\b", "Id de job inválido 'a\\\\b': contiene separadores de ruta.")

    def test_rejects_uppercase(self):
        self.assertRejected("12-Add", "Id de job inválido '12-Add': solo se permiten caracteres [0-9a-z-].")

    def test_rejects_empty(self):
        self.assertRejected("", "Id de job inválido '': está vacío.")

    def test_rejects_non_string(self):
        self.assertRejected(12, "Id de job inválido 12: debe ser texto.")


if __name__ == "__main__":
    unittest.main()
