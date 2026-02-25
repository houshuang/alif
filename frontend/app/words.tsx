import { useEffect } from "react";
import { useRouter } from "expo-router";

export default function WordsRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/explore");
  }, []);
  return null;
}
