import { redirect } from "next/navigation";

export default function SchemaIndex() {
  redirect("/schema/latest");
}
