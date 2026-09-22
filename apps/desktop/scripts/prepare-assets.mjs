import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { validatePetPackage } from "../../pet/package/validate.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const sourcePackage = resolve(here, "../../pet/package");
validatePetPackage(sourcePackage);
const source = resolve(sourcePackage, "spritesheet.webp");
const target = resolve(here, "../renderer/assets/spritesheet.webp");
mkdirSync(dirname(target), { recursive: true });
copyFileSync(source, target);
console.log(`prepared ${target}`);
