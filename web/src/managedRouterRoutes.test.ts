import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { matchRoutes } from "react-router-dom";
import * as ts from "typescript";
import { describe, expect, it } from "vitest";

const appPath = resolve(process.cwd(), "src/App.tsx");
const source = ts.createSourceFile(
  appPath,
  readFileSync(appPath, "utf8"),
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);

function routePathExpressions(): ts.Expression[] {
  const paths: ts.Expression[] = [];
  function visit(node: ts.Node): void {
    if (
      (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) &&
      node.tagName.getText(source) === "Route"
    ) {
      const path = node.attributes.properties.find(
        (attribute): attribute is ts.JsxAttribute =>
          ts.isJsxAttribute(attribute) && attribute.name.getText(source) === "path",
      );
      if (path?.initializer) {
        if (ts.isJsxExpression(path.initializer) && path.initializer.expression) {
          paths.push(path.initializer.expression);
        } else if (ts.isStringLiteral(path.initializer)) {
          paths.push(path.initializer);
        } else {
          throw new Error(`Unsupported Route path: ${path.getText(source)}`);
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  return paths;
}

function resolvePath(expression: ts.Expression, basename: string | undefined): string {
  if (ts.isStringLiteralLike(expression)) return expression.text;
  if (ts.isTemplateExpression(expression)) {
    return expression.templateSpans.reduce(
      (path, span) => path + resolvePath(span.expression, basename) + span.literal.text,
      expression.head.text,
    );
  }
  if (ts.isIdentifier(expression) && expression.text === "prefix") return basename ?? "";
  if (ts.isIdentifier(expression) && expression.text === "basename") return basename ?? "";
  if (
    ts.isBinaryExpression(expression) &&
    expression.operatorToken.kind === ts.SyntaxKind.BarBarToken
  ) {
    return resolvePath(expression.left, basename) || resolvePath(expression.right, basename);
  }
  if (ts.isConditionalExpression(expression)) {
    return resolvePath(expression.condition, basename)
      ? resolvePath(expression.whenTrue, basename)
      : resolvePath(expression.whenFalse, basename);
  }
  if (ts.isParenthesizedExpression(expression)) return resolvePath(expression.expression, basename);
  throw new Error(`Unsupported Route path expression: ${expression.getText(source)}`);
}

function samplePath(pattern: string): string {
  const segments = pattern.split("/").filter(Boolean);
  const path = segments.map((segment) => {
    if (segment === "*") return "unknown";
    if (segment.startsWith(":")) return "value";
    return segment;
  });
  return path.length ? `/${path.join("/")}` : "/";
}

describe("Databricks host route grammar", () => {
  it("uses the same matching behavior as React Router 6.4.1", () => {
    expect(
      matchRoutes([{ path: "/settings/:section/:subSection?" }], "/settings/general"),
    ).toBeNull();
  });

  it.each([undefined, "/omnigent", "/ml/omnigents"])(
    "matches every App route under basename %s",
    (basename) => {
      const expressions = routePathExpressions();
      expect(expressions.length).toBeGreaterThan(0);
      for (const expression of expressions) {
        const pattern = resolvePath(expression, basename);
        expect(
          pattern,
          `Route ${expression.getText(source)} uses unsupported optional syntax`,
        ).not.toContain("?");
        const location = samplePath(pattern);
        expect(
          matchRoutes([{ path: pattern }], location),
          `React Router 6.4.1 did not match ${location} against ${pattern}`,
        ).not.toBeNull();
      }
    },
  );
});
