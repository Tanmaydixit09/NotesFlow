
pipeline {
    agent any
 
    environment {
        IMAGE_NAME     = 'noteflow-app'
        IMAGE_TAG      = "${BUILD_NUMBER}"
        DOCKERHUB_USER = 'tanmaydixit09'
        PYTHON         = 'C:\\Users\\tanma\\AppData\\Local\\Programs\\Python\\Python314\\python.exe'
    }
 
    stages {
 
        stage('Checkout') {
            steps {
                echo '📥 Checking out code from GitHub...'
                checkout scm
            }
        }
 
        stage('Install Dependencies') {
            steps {
                echo '📦 Installing Python dependencies...'
                bat """
                    "%PYTHON%" --version
                    "%PYTHON%" -m pip install --upgrade pip
                    "%PYTHON%" -m pip install -r requirements.txt
                    "%PYTHON%" -m pip install pytest
                """
            }
        }
 
        stage('Run Tests') {
    steps {
        bat '''
        set TESTING=1
        "%PYTHON%" -m pytest test_app.py -v --tb=short
        '''
    }
}
 
        stage('Build Docker Image') {
            steps {
                echo '🐳 Building Docker image...'
                bat """
                    docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .
                    docker tag ${IMAGE_NAME}:${IMAGE_TAG} ${IMAGE_NAME}:latest
                """
            }
        }
 
        stage('Push to Docker Hub') {
            steps {
                echo '🚀 Pushing image to Docker Hub...'
                withCredentials([usernamePassword(
                    credentialsId: 'dockerhub-credentials',
                    usernameVariable: 'DOCKER_USER',
                    passwordVariable: 'DOCKER_PASS'
                )]) {
                    bat """
                        docker login -u %DOCKER_USER% -p %DOCKER_PASS%
                        docker tag ${IMAGE_NAME}:latest %DOCKER_USER%/${IMAGE_NAME}:latest
                        docker tag ${IMAGE_NAME}:latest %DOCKER_USER%/${IMAGE_NAME}:${IMAGE_TAG}
                        docker push %DOCKER_USER%/${IMAGE_NAME}:latest
                        docker push %DOCKER_USER%/${IMAGE_NAME}:${IMAGE_TAG}
                    """
                }
            }
        }
 
        stage('Deploy to Kubernetes') {
            steps {
                echo '☸️ Deploying to Kubernetes...'
                bat '''
                    kubectl apply -f k8s/deployment.yaml
                    kubectl apply -f k8s/service.yaml
                    kubectl rollout status deployment/noteflow-deployment
                '''
            }
        }
 
    }
 
    post {
        success {
            echo '✅ Pipeline completed! NoteFlow is live.'
        }
        failure {
            echo '❌ Pipeline failed. Check the logs above.'
        }
        always {
            echo '🧹 Cleaning up...'
            bat "docker rmi ${IMAGE_NAME}:${IMAGE_TAG} || ver > nul"
        }
    }
}
 
