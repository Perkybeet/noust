// A Go server with the standard library only. Noust builds it with the server's Go and runs the binary.
package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, "Hello from Noust (go)")
	})
	log.Fatal(http.ListenAndServe("127.0.0.1:"+port, nil))
}
