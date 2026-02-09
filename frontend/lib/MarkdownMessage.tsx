import Markdown from "react-native-markdown-display";
import { colors, fonts } from "./theme";

interface MarkdownMessageProps {
  content: string;
  textColor?: string;
}

const baseStyle: Record<string, any> = {
  body: {
    fontSize: fonts.body,
    lineHeight: 22,
    marginTop: 0,
    marginBottom: 0,
  },
  paragraph: {
    marginTop: 0,
    marginBottom: 8,
  },
  strong: {
    fontWeight: "700",
  },
  em: {
    fontStyle: "italic",
  },
  heading1: {
    fontSize: 20,
    marginTop: 0,
    marginBottom: 8,
    fontWeight: "700",
  },
  heading2: {
    fontSize: 18,
    marginTop: 0,
    marginBottom: 8,
    fontWeight: "700",
  },
  heading3: {
    fontSize: 16,
    marginTop: 0,
    marginBottom: 6,
    fontWeight: "700",
  },
  bullet_list: {
    marginTop: 0,
    marginBottom: 8,
  },
  ordered_list: {
    marginTop: 0,
    marginBottom: 8,
  },
  list_item: {
    marginBottom: 4,
  },
  code_inline: {
    backgroundColor: colors.surface,
    borderRadius: 4,
    paddingHorizontal: 4,
    paddingVertical: 2,
    fontSize: fonts.small,
  },
  fence: {
    backgroundColor: colors.surface,
    borderRadius: 6,
    padding: 10,
    marginVertical: 8,
  },
  code_block: {
    fontSize: fonts.small,
    lineHeight: 20,
  },
  blockquote: {
    borderLeftWidth: 3,
    borderLeftColor: colors.border,
    paddingLeft: 10,
    marginLeft: 0,
  },
  link: {
    color: colors.accent,
    textDecorationLine: "underline",
  },
};

export default function MarkdownMessage({
  content,
  textColor = colors.text,
}: MarkdownMessageProps) {
  const style: Record<string, any> = {
    ...baseStyle,
    body: { ...baseStyle.body, color: textColor },
    paragraph: { ...baseStyle.paragraph, color: textColor },
    strong: { ...baseStyle.strong, color: textColor },
    em: { ...baseStyle.em, color: textColor },
    heading1: { ...baseStyle.heading1, color: textColor },
    heading2: { ...baseStyle.heading2, color: textColor },
    heading3: { ...baseStyle.heading3, color: textColor },
    list_item: { ...baseStyle.list_item, color: textColor },
    code_inline: { ...baseStyle.code_inline, color: textColor },
    fence: { ...baseStyle.fence, color: textColor },
    code_block: { ...baseStyle.code_block, color: textColor },
    blockquote: { ...baseStyle.blockquote, color: textColor },
  };

  return <Markdown style={style}>{content}</Markdown>;
}
