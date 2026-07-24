allprojects {
    repositories {
        google()
        mavenCentral()
    }
}

val newBuildDir: Directory =
    rootProject.layout.buildDirectory
        .dir("../../build")
        .get()
rootProject.layout.buildDirectory.value(newBuildDir)

subprojects {
    val newSubprojectBuildDir: Directory = newBuildDir.dir(project.name)
    project.layout.buildDirectory.value(newSubprojectBuildDir)
}
subprojects {
    project.evaluationDependsOn(":app")
}

// Phase 4 (webview_flutter) pulls in newer androidx (fragment 1.7.x, core 1.13.x,
// window 1.2.x …) whose AAR metadata requires compileSdk 34+. Older plugins such
// as flutter_pcm_sound hard-code compileSdkVersion 33, so their :checkAarMetadata
// fails once Gradle resolves those transitive deps to the higher versions. Bump
// any Android subproject still below 35 to keep every module consistent. Done via
// reflection so it stays AGP-version-agnostic (no AGP DSL types referenced).
fun Project.bumpCompileSdkIfStale() {
    val android = extensions.findByName("android") ?: return
    val currentSdk = runCatching {
        val getter = android.javaClass.getMethod("getCompileSdkVersion")
        (getter.invoke(android) as? String)
            ?.substringAfter("android-")
            ?.toIntOrNull()
    }.getOrNull() ?: 0
    if (currentSdk < 35) {
        runCatching {
            val setter =
                android.javaClass.getMethod(
                    "compileSdkVersion",
                    Int::class.javaPrimitiveType,
                )
            setter.invoke(android, 35)
        }
    }
}

subprojects {
    // The earlier evaluationDependsOn(":app") may have already evaluated some
    // subprojects, so afterEvaluate would throw for those — apply inline instead.
    if (state.executed) {
        bumpCompileSdkIfStale()
    } else {
        afterEvaluate { bumpCompileSdkIfStale() }
    }
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
