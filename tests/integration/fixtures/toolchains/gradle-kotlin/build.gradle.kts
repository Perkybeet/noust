// A Kotlin program on the JDK's own HTTP server, packaged the way Gradle's application plugin
// does: a directory with the program's jar, its libraries and a start script.
plugins {
    kotlin("jvm") version "2.0.21"
    application
}

repositories {
    mavenCentral()
}

kotlin {
    jvmToolchain(21)
}

application {
    mainClass.set("app.MainKt")
}
