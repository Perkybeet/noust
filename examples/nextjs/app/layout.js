export const metadata = { title: "Hello from Noust (nextjs)" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
